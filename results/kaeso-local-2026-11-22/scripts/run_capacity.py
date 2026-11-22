"""Capacity pass: the same load-probe at 64 clients against a zero-delay mock.

With MOCK_TTFT_MS=0 and MOCK_ITL_MS=0 the mock answers at once, so 64 closed-loop
clients drive each gateway (2 pinned cores) to its own limit instead of waiting on
the mock's 22 ms. Each round first measures the direct-to-mock ceiling with the same
driver; a gateway result means something only while it stays well under that ceiling.
Dependencies the gateway needs (Kaeso's Postgres, Valkey and ClickHouse) are sampled
too and reported next to the gateway's own CPU and memory.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root")
import run  # noqa: E402

OUT = Path("/root/cap")
ROUNDS = int(os.environ.get("ROUNDS", "3"))
COUNTS = {"direct": 60000, "litellm-python": 3000, "portkey": 15000}
DEFAULT_COUNT = 40000


def deps_samplers(target: dict) -> dict[str, run.Sampler]:
    out = {}
    for name in target.get("cleanup", []):
        if subprocess.run(f"docker inspect {name}", shell=True, capture_output=True).returncode == 0:
            out[name] = run.Sampler(name)
    return out


def measure(name: str, target: dict, rnd: int, sampler, deps) -> dict:
    d = OUT / f"r{rnd}" / name
    d.mkdir(parents=True, exist_ok=True)
    count = COUNTS.get(name, DEFAULT_COUNT)
    run.probe(target, f"{name}-warm", 64, max(count // 10, 500), d / "warm.json")
    for s in deps.values():
        s.__enter__()
    try:
        if sampler:
            with sampler:
                r = run.probe(target, f"{name}-cap", 64, count, d / "cap.json")
            r["resources"] = sampler.summary()
        else:
            r = run.probe(target, f"{name}-cap", 64, count, d / "cap.json")
    finally:
        for s in deps.values():
            s.__exit__(None, None, None)
    r["deps"] = {k: s.summary() for k, s in deps.items()}
    r["error_count"] = len(r.pop("errors"))
    return r


def main() -> None:
    names = sys.argv[1:] or list(run.GATEWAYS)
    OUT.mkdir(exist_ok=True)
    run.sh("pkill -f mock-upstream", check=False)
    env = {**os.environ, "MOCK_TTFT_MS": "0", "MOCK_ITL_MS": "0"}
    mock = subprocess.Popen(run.pinned([str(run.BENCH / "target/release/mock-upstream")]), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run.wait_http(f"{run.MOCK}/health")
    path = OUT / "results.json"
    results = json.loads(path.read_text()) if path.exists() else {}
    try:
        for rnd in range(1, ROUNDS + 1):
            order = names[:]
            random.Random(100 + rnd).shuffle(order)
            for name in order:
                key = f"r{rnd}:{name}"
                if key in results:
                    continue
                direct = measure("direct", run.DIRECT, rnd, None, {})
                target = run.GATEWAYS[name]()
                try:
                    gw = measure(name, target, rnd, run.Sampler("gw"), deps_samplers(target))
                finally:
                    run.docker_rm("gw", *target.get("cleanup", []))
                results[key] = {"gateway": gw, "direct": direct}
                path.write_text(json.dumps(results, indent=1))
                print(f"[{time.strftime('%H:%M:%S')}] r{rnd} {name}: {gw['rps']:.0f} req/s "
                      f"(direct ceiling {direct['rps']:.0f}) p50 {gw['latency_p50_ms']:.2f} p99 {gw['latency_p99_ms']:.2f} "
                      f"cpu {gw['resources']['avg_cpu_percent']:.0f}% deps "
                      f"{ {k: round(v['avg_cpu_percent']) for k, v in gw['deps'].items()} } errors {gw['error_count']}",
                      flush=True)
    finally:
        mock.terminate()
    print("CAP_DONE", flush=True)


if __name__ == "__main__":
    main()
