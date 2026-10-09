"""Summarise the hosted real-model run into the CSVs the hosted chart reads.

Writes results/hosted_ttft.csv (per-arm percentiles plus p50/p90 deltas against direct Anthropic and
against LiteLLM Rust, with 10,000-resample bootstrap 95% CIs and a two-sided permutation p-value) and
results/openrouter_overhead.csv (client TTFT minus OpenRouter's self-reported upstream latency).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

RESULTS = Path(__file__).resolve().parent.parent / "results"
ARMS = ["direct", "litellm-rust", "litellm-python", "openrouter"]
B = 10_000
rng = np.random.default_rng(0)


def delta(x: np.ndarray, y: np.ndarray, q: float) -> tuple[float, float, float, float]:
    obs = np.percentile(x, q) - np.percentile(y, q)
    boot = np.percentile(rng.choice(x, (B, len(x))), q, axis=1) - np.percentile(rng.choice(y, (B, len(y))), q, axis=1)
    pool = np.concatenate([x, y])
    perm = np.empty(B)
    for i in range(B):
        rng.shuffle(pool)
        perm[i] = np.percentile(pool[: len(x)], q) - np.percentile(pool[len(x):], q)
    p = (np.sum(np.abs(perm) >= abs(obs)) + 1) / (B + 1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(obs), float(lo), float(hi), float(p)


def main() -> None:
    rows = [json.loads(line) for line in open(RESULTS / "hosted_messages_raw.jsonl")]
    ok = {a: [r for r in rows if r["arm"] == a and not r["err"]] for a in ARMS}
    ttft = {a: np.array([r["ttft_ms"] for r in ok[a]]) for a in ARMS}
    ttlt = {a: np.array([r["ttlt_ms"] for r in ok[a]]) for a in ARMS}
    out = []
    for a in ARMS:
        n_all = sum(1 for r in rows if r["arm"] == a)
        row = {"gateway": a, "requests": n_all, "errors": n_all - len(ok[a]),
               "new_connections": sum(1 for r in rows if r["arm"] == a and r["new_conn"]),
               **{f"ttft_p{q}_ms": round(float(np.percentile(ttft[a], q)), 1) for q in (50, 90, 99)},
               "ttlt_p50_ms": round(float(np.percentile(ttlt[a], 50)), 1)}
        for ref in ("direct", "litellm-rust"):
            for q in (50, 90):
                if a == ref:
                    vals = ("", "", "", "")
                else:
                    vals = tuple(round(v, 4 if i == 3 else 1) for i, v in enumerate(delta(ttft[a], ttft[ref], q)))
                for k, v in zip(("delta", "ci_lo", "ci_hi", "perm_p"), vals):
                    row[f"p{q}_{k}_vs_{ref}_ms" if k != "perm_p" else f"p{q}_perm_p_vs_{ref}"] = v
        out.append(row)
    with (RESULTS / "hosted_ttft.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)

    gen_path = RESULTS / "openrouter_generation.jsonl"
    if gen_path.exists():
        gen = {d["id"]: d for d in map(json.loads, open(gen_path))}
        pairs = [(r["ttft_ms"], gen[r["id"]]) for r in ok["openrouter"] if r["id"] in gen]
        own = np.array([t - g["latency"] for t, g in pairs if g.get("latency") is not None])
        upstream = np.array([g["latency"] for _, g in pairs if g.get("latency") is not None])
        providers = sorted({g.get("provider_name") for _, g in pairs})
        with (RESULTS / "openrouter_overhead.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["metric", "p10_ms", "p50_ms", "p90_ms", "n", "providers"])
            for name, v in (("openrouter_reported_upstream_latency", upstream),
                            ("client_ttft_minus_reported_latency", own)):
                w.writerow([name, *(round(float(np.percentile(v, q)), 1) for q in (10, 50, 90)), len(v),
                            "|".join(map(str, providers))])
    for row in out:
        print({k: v for k, v in row.items() if "vs_litellm" not in k})


if __name__ == "__main__":
    main()
