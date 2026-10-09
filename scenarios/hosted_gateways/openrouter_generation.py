"""Fetch OpenRouter's own per-request metadata (provider, upstream latency) for the hosted run.

    python scenarios/hosted_gateways/openrouter_generation.py results/hosted_messages_raw.jsonl \
        results/openrouter_generation.jsonl
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

KEEP = ["id", "created_at", "model", "provider_name", "router", "data_region", "service_tier", "streamed",
        "latency", "generation_time", "moderation_latency", "tokens_prompt", "tokens_completion",
        "native_tokens_prompt", "native_tokens_completion", "finish_reason", "total_cost"]


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    ids = [d["id"] for d in map(json.loads, open(src)) if d["arm"] == "openrouter" and d["id"] and not d["err"]]
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    with httpx.Client(timeout=30, headers=headers) as c, open(dst, "w") as out:
        for gen_id in ids:
            for attempt in range(5):
                r = c.get("https://openrouter.ai/api/v1/generation", params={"id": gen_id})
                if r.status_code == 200:
                    data = r.json()["data"]
                    out.write(json.dumps({k: data.get(k) for k in KEEP}) + "\n")
                    break
                time.sleep(2 * (attempt + 1))
            else:
                print(f"missing {gen_id}: HTTP {r.status_code}", file=sys.stderr)


if __name__ == "__main__":
    main()
