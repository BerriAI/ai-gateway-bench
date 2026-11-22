"""Summarize run_capacity.py: median over rounds, min to max in brackets."""
import json, statistics, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, str(Path(__file__).parent))
SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
data = json.loads((SRC / "results.json").read_text())
LABELS = {"kaeso": "Kaeso 0.7.0", "kaeso-logs": "Kaeso 0.7.0 (request logs on)", "litellm-rust": "LiteLLM Rust (main d4e8f96)",
          "bifrost": "Bifrost 2.6.0", "portkey": "Portkey 1.15.2", "litellm-python": "LiteLLM Python 1.110.2"}
COLORS = {"kaeso": "#2563eb", "kaeso-logs": "#60a5fa", "litellm-rust": "#16a34a", "bifrost": "#ea580c", "portkey": "#7c3aed", "litellm-python": "#64748b"}
def st(v): return {"median": statistics.median(v), "min": min(v), "max": max(v), "n": len(v)}
summary = {}
for gw in LABELS:
    rs = [v for k, v in sorted(data.items()) if k.split(":", 1)[1] == gw]
    if not rs: continue
    g = [r["gateway"] for r in rs]
    summary[gw] = {
        "rps": st([x["rps"] for x in g]),
        "direct_ceiling_rps": st([r["direct"]["rps"] for r in rs]),
        "p50_ms": st([x["latency_p50_ms"] for x in g]),
        "p99_ms": st([x["latency_p99_ms"] for x in g]),
        "gw_cpu_us": st([x["resources"]["avg_cpu_percent"] / 100 / x["rps"] * 1e6 for x in g]),
        "deps_cpu_us": st([sum(d["avg_cpu_percent"] for d in x["deps"].values()) / 100 / x["rps"] * 1e6 for x in g]),
        "gw_cpu_pct": st([x["resources"]["avg_cpu_percent"] for x in g]),
        "deps_cpu_pct": st([sum(d["avg_cpu_percent"] for d in x["deps"].values()) for x in g]),
        "gw_peak_mb": max(x["resources"]["peak_rss_mb"] for x in g),
        "deps_peak_mb": max(sum(d["peak_rss_mb"] for d in x["deps"].values()) for x in g),
        "errors": sum(x["error_count"] for x in g), "requests": sum(x["count"] for x in g),
    }
(OUT / "capacity.json").write_text(json.dumps(summary, indent=1))
c = lambda s, d=0: f"{s['median']:,.{d}f} ({s['min']:,.{d}f} to {s['max']:,.{d}f})"
lines = ["| Gateway | Req/s at 64 clients | Share of direct ceiling | Latency p50 / p99 ms | Gateway CPU µs/req | Dependency CPU µs/req | Gateway peak MB | Dependency peak MB | Errors |", "|---|---|---|---|---|---|---|---|---|"]
for gw, s in sorted(summary.items(), key=lambda x: -x[1]["rps"]["median"]):
    lines.append(f"| {LABELS[gw]} | {c(s['rps'])} | {s['rps']['median'] / s['direct_ceiling_rps']['median']:.0%} | {s['p50_ms']['median']:.2f} / {s['p99_ms']['median']:.2f} | "
                 f"{c(s['gw_cpu_us'])} | {s['deps_cpu_us']['median']:,.0f} | {s['gw_peak_mb']:,.0f} | {s['deps_peak_mb']:,.0f} | {s['errors']}/{s['requests']:,} |")
lines.append(f"\nDirect ceiling (same driver, no gateway): {c(next(iter(summary.values()))['direct_ceiling_rps'])} req/s")
(OUT / "capacity.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
order = sorted(summary, key=lambda g: summary[g]["rps"]["median"])
fig, ax = plt.subplots(figsize=(9, 0.55 * len(order) + 1.6))
vals = [summary[g]["rps"]["median"] for g in order]
ax.barh([LABELS[g] for g in order], vals, color=[COLORS[g] for g in order], height=0.6)
for i, g in enumerate(order):
    s = summary[g]["rps"]
    ax.plot([s["min"], s["max"]], [i, i], color="#111", lw=1)
    ax.text(s["max"], i, f"  {s['median']:,.0f}", va="center", fontsize=9)
ax.axvline(summary[order[0]]["direct_ceiling_rps"]["median"], color="#999", ls="--", lw=1)
ax.set_title("Capacity on 2 pinned cores, 64 clients, zero-delay mock", loc="left", fontweight="bold")
ax.set_xlabel("requests per second, median of 3 rounds, line = min to max (higher is better); dashed = direct ceiling")
for sp in ("top", "right"): ax.spines[sp].set_visible(False)
fig.tight_layout(); fig.savefig(OUT / "capacity.png", dpi=160)
