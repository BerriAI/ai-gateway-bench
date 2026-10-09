"""Real-model streaming /v1/messages TTFT: direct Anthropic vs LiteLLM (Rust, Python) vs OpenRouter.

OpenRouter is hosted and cannot point at the local mock, so this scenario uses a real model at low
throughput. Every arm keeps one warm persistent client; requests are shuffled across arms so upstream
drift hits all arms equally. TTFT is the first `text_delta`, not the first byte (`message_start`).

    python scenarios/hosted_gateways/run.py --out results/hosted_messages_raw.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common.sse import _parse_event  # noqa: E402

PROMPT = "Count from 1 to 10."
MAX_TOKENS = 64


def arms(rust_base: str, python_base: str, gateway_key: str) -> dict[str, tuple[str, dict, dict]]:
    body = {"max_tokens": MAX_TOKENS, "stream": True, "messages": [{"role": "user", "content": PROMPT}]}
    version = {"anthropic-version": "2023-06-01"}
    gateway = {**version, "Authorization": f"Bearer {gateway_key}"}
    return {
        "direct": ("https://api.anthropic.com/v1/messages", {**body, "model": "claude-haiku-4-5"},
                   {**version, "x-api-key": os.environ["ANTHROPIC_API_KEY"]}),
        "litellm-rust": (f"{rust_base}/v1/messages", {**body, "model": "claude-haiku-4.5"}, gateway),
        "litellm-python": (f"{python_base}/v1/messages", {**body, "model": "claude-haiku-4.5"}, gateway),
        "openrouter": ("https://openrouter.ai/api/v1/messages",
                       {**body, "model": "anthropic/claude-haiku-4.5",
                        "provider": {"order": ["anthropic"], "allow_fallbacks": False}},
                       {**version, "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}),
    }


def client() -> httpx.Client:
    return httpx.Client(limits=httpx.Limits(max_connections=100, max_keepalive_connections=100, keepalive_expiry=600),
                        timeout=httpx.Timeout(120, connect=5.0))


def call(c: httpx.Client, url: str, body: dict, headers: dict) -> dict:
    new_conn = False

    def trace(event: str, _info: dict) -> None:
        nonlocal new_conn
        if event == "connection.connect_tcp.started":
            new_conn = True

    rec: dict = {"ttft_ms": None, "ttlt_ms": None, "status": 0, "err": None, "id": None, "text_deltas": 0}
    started = time.perf_counter()
    try:
        with c.stream("POST", url, json=body, headers=headers, extensions={"trace": trace}) as r:
            rec["status"] = r.status_code
            rec["request_id"] = r.headers.get("request-id") or r.headers.get("x-request-id")
            rec["cf_ray"] = r.headers.get("cf-ray")
            buf = b""
            for piece in r.iter_bytes():
                buf += piece
                while b"\n\n" in buf:
                    raw, _, buf = buf.partition(b"\n\n")
                    parsed = _parse_event(raw.decode("utf-8", errors="replace"))
                    if parsed is None or not isinstance(parsed[1], dict):
                        continue
                    data = parsed[1]
                    kind = data.get("type")
                    if kind == "message_start":
                        rec["id"] = data.get("message", {}).get("id")
                    elif kind == "content_block_delta" and data.get("delta", {}).get("type") == "text_delta":
                        rec["text_deltas"] += 1
                        if rec["ttft_ms"] is None:
                            rec["ttft_ms"] = (time.perf_counter() - started) * 1000
                    elif kind == "message_delta":
                        rec["output_tokens"] = data.get("usage", {}).get("output_tokens")
                    elif kind == "error":
                        rec["err"] = json.dumps(data)[:300]
            rec["ttlt_ms"] = (time.perf_counter() - started) * 1000
            if r.is_error:
                rec["err"] = rec["err"] or f"HTTP {r.status_code}"
            elif rec["ttft_ms"] is None:
                rec["err"] = rec["err"] or "no text_delta"
    except Exception as exc:  # recorded, not raised: an arm's failures are part of the result
        rec["err"] = f"{type(exc).__name__}: {exc}"[:300]
    rec["new_conn"] = new_conn
    return rec


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--per-arm", type=int, default=300)
    p.add_argument("--clients", type=int, default=2)
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--arms", default="direct,litellm-rust,litellm-python,openrouter")
    p.add_argument("--rust-base", default="http://127.0.0.1:4100")
    p.add_argument("--python-base", default="http://127.0.0.1:4010")
    p.add_argument("--gateway-key", default=os.environ.get("LITELLM_MASTER_KEY", "sk-bench"))
    a = p.parse_args()
    spec = arms(a.rust_base, a.python_base, a.gateway_key)
    selected = a.arms.split(",")
    clients = {arm: client() for arm in selected}
    for arm in selected:
        for _ in range(a.warmup):
            call(clients[arm], *spec[arm])
    schedule = [arm for arm in selected for _ in range(a.per_arm)]
    random.Random(a.seed).shuffle(schedule)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    queue = list(enumerate(schedule))

    def worker() -> None:
        while True:
            with lock:
                if not queue:
                    return
                i, arm = queue.pop(0)
            t = time.time()
            rec = {"i": i, "arm": arm, "t": t, **call(clients[arm], *spec[arm])}
            with lock, out.open("a") as f:
                f.write(json.dumps(rec) + "\n")

    threads = [threading.Thread(target=worker) for _ in range(a.clients)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()


if __name__ == "__main__":
    main()
