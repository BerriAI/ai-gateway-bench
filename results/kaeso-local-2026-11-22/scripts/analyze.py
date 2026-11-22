"""Aggregate run.py's results.json into tables and charts.

Each number is gateway minus the direct-to-mock run taken right before it in the
same round; the reported value is the median over rounds, with the min and max.
Cost uses the bench's own formula: (CPU fraction x $0.04/vCPU-h + peak RSS GB x
$0.005/GB-h) / requests per hour, scaled to 1M requests.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
data = json.loads((SRC / "results.json").read_text())

LABELS = {
    "kaeso": "Kaeso 0.7.0",
    "kaeso-logs": "Kaeso 0.7.0 (request logs on)",
    "litellm-rust": "LiteLLM Rust (main d4e8f96)",
    "bifrost": "Bifrost 2.6.0",
    "portkey": "Portkey 1.15.2",
    "litellm-python": "LiteLLM Python 1.110.2",
}
COLORS = {
    "kaeso": "#2563eb",
    "kaeso-logs": "#60a5fa",
    "litellm-rust": "#16a34a",
    "bifrost": "#ea580c",
    "portkey": "#7c3aed",
    "litellm-python": "#64748b",
}
GWS = [g for g in LABELS if any(k.endswith(":" + g) for k in data)]
CONCS = ["1", "4", "16", "64"]


def rounds(gw: str):
    return [v for k, v in sorted(data.items()) if k.split(":", 1)[1] == gw]


def stat(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"median": statistics.median(values), "min": min(values), "max": max(values), "n": len(values)}


summary: dict = {}
for gw in GWS:
    rs = rounds(gw)
    s: dict = {"label": LABELS[gw], "sweep": {}}
    for c in CONCS:
        g = [r["gateway"]["sweep"][c] for r in rs]
        d = [r["direct"]["sweep"][c] for r in rs]
        s["sweep"][c] = {
            "p50_added_ms": stat([a["latency_p50_ms"] - b["latency_p50_ms"] for a, b in zip(g, d)]),
            "p99_added_ms": stat([a["latency_p99_ms"] - b["latency_p99_ms"] for a, b in zip(g, d)]),
            "rps": stat([a["rps"] for a in g]),
            "direct_rps": stat([b["rps"] for b in d]),
            "errors": sum(a["error_count"] for a in g),
            "requests": sum(a["count"] for a in g),
            "cpu_percent": stat([a["resources"]["avg_cpu_percent"] for a in g]),
            "peak_rss_mb": stat([a["resources"]["peak_rss_mb"] for a in g]),
        }
    st = [r["gateway"]["stream"] for r in rs]
    sd = [r["direct"]["stream"] for r in rs]
    ok = [(a, b) for a, b in zip(st, sd) if a and "failed" not in a and a.get("ttft_ms", {}).get("p50") is not None]
    s["stream"] = {
        "available": bool(ok),
        "ttft_p50_added_ms": stat([a["ttft_ms"]["p50"] - b["ttft_ms"]["p50"] for a, b in ok]),
        "ttft_p99_added_ms": stat([a["ttft_ms"]["p99"] - b["ttft_ms"]["p99"] for a, b in ok]),
        "inter_chunk_p99_ms": stat([a["inter_chunk_ms"]["p99"] for a, _ in ok]),
        "direct_inter_chunk_p99_ms": stat([b["inter_chunk_ms"]["p99"] for _, b in ok]),
        "chunk_fidelity": stat([a["chunk_fidelity"] / b["chunk_fidelity"] for a, b in ok if a["chunk_fidelity"] and b["chunk_fidelity"]]),
        "errors": sum(a.get("error_count", 0) for a in st if a and "failed" not in a),
        "error_sample": next((a.get("error_sample") for a in st if a and a.get("error_sample")), None)
        or next((a.get("failed") for a in st if a and a.get("failed")), None),
    }
    s["sessions"] = {}
    for key in ("claude_code_nonstream", "codex_nonstream", "claude_code_stream", "codex_stream"):
        pairs = [(r["gateway"]["sessions"].get(key, {}), r["direct"]["sessions"].get(key, {})) for r in rs]
        good = [(a, b) for a, b in pairs if "total_wall_time_ms" in a and a["error_count"] == 0]
        s["sessions"][key] = {
            "added_ms": stat([a["total_wall_time_ms"] - b["total_wall_time_ms"] for a, b in good]),
            "rounds_ok": len(good),
            "rounds": len(pairs),
            "invalid_tool_turns": sum(a.get("invalid_tool_turns", 0) for a, _ in pairs),
            "error_sample": next((a.get("error_sample") or a.get("failed") for a, _ in pairs if a.get("error_count") or a.get("failed")), None),
        }
    s["idle_rss_mb"] = stat([r["gateway"].get("idle_rss_mb") for r in rs])
    peak = max(s["sweep"][c]["peak_rss_mb"]["max"] for c in CONCS)
    c64 = s["sweep"]["64"]
    cpu_frac = c64["cpu_percent"]["median"] / 100
    rps = c64["rps"]["median"]
    hourly = cpu_frac * 0.04 + (peak / 1000) * 0.005
    s["peak_rss_mb"] = peak
    s["c64_hourly_usd"] = hourly
    s["usd_per_million"] = hourly / (rps * 3600) * 1_000_000
    s["rps_per_dollar"] = rps / hourly
    s["cpu_us_per_request_c64"] = cpu_frac / rps * 1_000_000
    summary[gw] = s

(OUT / "summary.json").write_text(json.dumps(summary, indent=1))


def fmt(x, d=2):
    return "n/a" if x is None else f"{x:.{d}f}"


def cell(st, d=2):
    if st is None:
        return "n/a"
    return f"{st['median']:.{d}f} ({st['min']:.{d}f} to {st['max']:.{d}f})"


rows = []
for gw in GWS:
    s = summary[gw]
    rows.append({
        "gateway": gw,
        **{f"p50_added_c{c}_ms": round(s["sweep"][c]["p50_added_ms"]["median"], 3) for c in CONCS},
        **{f"p99_added_c{c}_ms": round(s["sweep"][c]["p99_added_ms"]["median"], 3) for c in CONCS},
        "rps_c64": round(s["sweep"]["64"]["rps"]["median"], 1),
        "cpu_percent_c64": round(s["sweep"]["64"]["cpu_percent"]["median"], 1),
        "cpu_us_per_request_c64": round(s["cpu_us_per_request_c64"], 1),
        "idle_rss_mb": round(s["idle_rss_mb"]["median"], 1) if s["idle_rss_mb"] else "",
        "peak_rss_mb": round(s["peak_rss_mb"], 1),
        "ttft_p50_added_ms": round(s["stream"]["ttft_p50_added_ms"]["median"], 3) if s["stream"]["available"] else "",
        "ttft_p99_added_ms": round(s["stream"]["ttft_p99_added_ms"]["median"], 3) if s["stream"]["available"] else "",
        "claude_code_added_ms": round(s["sessions"]["claude_code_nonstream"]["added_ms"]["median"], 1) if s["sessions"]["claude_code_nonstream"]["added_ms"] else "",
        "codex_added_ms": round(s["sessions"]["codex_nonstream"]["added_ms"]["median"], 1) if s["sessions"]["codex_nonstream"]["added_ms"] else "",
        "usd_per_million": round(s["usd_per_million"], 6),
        "rps_per_dollar": round(s["rps_per_dollar"]),
        "errors": sum(s["sweep"][c]["errors"] for c in CONCS),
    })
with (OUT / "results.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

# ---------------------------------------------------------------- charts


def bars(metric, title, unit, fname, log=False):
    vals = [(gw, metric(summary[gw])) for gw in GWS]
    vals = [(g, v) for g, v in vals if v is not None]
    vals.sort(key=lambda x: x[1], reverse=True)
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(vals) + 1.4))
    ax.barh([LABELS[g] for g, _ in vals], [v for _, v in vals], color=[COLORS[g] for g, _ in vals], height=0.6)
    for i, (_, v) in enumerate(vals):
        ax.text(v, i, f"  {v:,.2f}" if v < 100 else f"  {v:,.0f}", va="center", fontsize=9)
    if log:
        ax.set_xscale("log")
    ax.set_title(title, loc="left", fontweight="bold")
    ax.set_xlabel(unit)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.set_xlim(right=max(v for _, v in vals) * (3 if log else 1.25))
    fig.tight_layout()
    fig.savefig(OUT / fname, dpi=160)
    plt.close(fig)


bars(lambda s: s["sweep"]["16"]["p50_added_ms"]["median"], "Added latency p50 at 16 concurrent clients", "ms over direct to mock (lower is better)", "added_p50_c16.png", log=True)
bars(lambda s: s["sweep"]["16"]["p99_added_ms"]["median"], "Added latency p99 at 16 concurrent clients", "ms over direct to mock (lower is better)", "added_p99_c16.png", log=True)
bars(lambda s: s["sweep"]["64"]["rps"]["median"], "Throughput at 64 concurrent clients (2 pinned cores)", "requests per second (higher is better)", "rps_c64.png")
bars(lambda s: s["cpu_us_per_request_c64"], "Gateway CPU per request at 64 clients", "CPU microseconds per request (lower is better)", "cpu_per_request.png", log=True)
bars(lambda s: s["peak_rss_mb"], "Peak memory (whole container process tree)", "MB (lower is better)", "peak_rss.png", log=True)
bars(lambda s: s["sessions"]["claude_code_nonstream"]["added_ms"]["median"] if s["sessions"]["claude_code_nonstream"]["added_ms"] else None,
     "Added wall time over a 30-turn Claude Code session", "ms over direct to mock (lower is better)", "session_claude_code.png", log=True)

fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
for ax, key, title in ((axes[0], "p50_added_ms", "p50"), (axes[1], "p99_added_ms", "p99")):
    for gw in GWS:
        ys = [max(summary[gw]["sweep"][c][key]["median"], 0.01) for c in CONCS]
        ax.plot([int(c) for c in CONCS], ys, marker="o", color=COLORS[gw], label=LABELS[gw])
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xticks([1, 4, 16, 64], ["1", "4", "16", "64"])
    ax.set_xlabel("concurrent clients")
    ax.set_ylabel(f"added {title} latency, ms (log)")
    ax.set_title(f"Added {title} latency vs concurrency", loc="left", fontweight="bold")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
axes[1].legend(fontsize=8, frameon=False)
fig.text(0.01, 0.005, "At 64 clients the driver and mock share 2 cores and the direct baseline's own tail rises, so the subtraction understates "
         "gateway overhead there. Gateway capacity is measured separately against a zero-delay mock.", fontsize=7.5, color="#555")
fig.tight_layout(rect=(0, 0.04, 1, 1))
fig.savefig(OUT / "latency_vs_concurrency.png", dpi=160)
plt.close(fig)

# ---------------------------------------------------------------- markdown

lines = ["| Gateway | Added p50 c1 | Added p50 c16 | Added p99 c16 | Added p99 c64 | RPS c64 | CPU µs/req | Peak RSS MB | Errors |", "|---|---|---|---|---|---|---|---|---|"]
for gw in sorted(GWS, key=lambda g: summary[g]["sweep"]["16"]["p50_added_ms"]["median"]):
    s = summary[gw]
    lines.append(
        f"| {s['label']} | {fmt(s['sweep']['1']['p50_added_ms']['median'], 3)} | {fmt(s['sweep']['16']['p50_added_ms']['median'], 3)} | "
        f"{fmt(s['sweep']['16']['p99_added_ms']['median'], 3)} | {fmt(s['sweep']['64']['p99_added_ms']['median'], 2)} | "
        f"{s['sweep']['64']['rps']['median']:,.0f} | {s['cpu_us_per_request_c64']:,.0f} | {s['peak_rss_mb']:,.0f} | "
        f"{sum(s['sweep'][c]['errors'] for c in CONCS)} |"
    )
lines += ["", "| Gateway | TTFT added p50 | TTFT added p99 | Inter-chunk p99 (direct) | Claude Code session added ms | Codex session added ms | Streamed sessions OK | $/1M req | RPS per $ |", "|---|---|---|---|---|---|---|---|---|"]
for gw in GWS:
    s = summary[gw]
    st = s["stream"]
    ss = s["sessions"]
    streamed = f"{ss['claude_code_stream']['rounds_ok'] + ss['codex_stream']['rounds_ok']}/{ss['claude_code_stream']['rounds'] + ss['codex_stream']['rounds']}"
    lines.append(
        f"| {s['label']} | {cell(st['ttft_p50_added_ms'])} | {cell(st['ttft_p99_added_ms'])} | "
        f"{fmt(st['inter_chunk_p99_ms']['median'] if st['inter_chunk_p99_ms'] else None)} ({fmt(st['direct_inter_chunk_p99_ms']['median'] if st['direct_inter_chunk_p99_ms'] else None)}) | "
        f"{cell(ss['claude_code_nonstream']['added_ms'], 1)} | {cell(ss['codex_nonstream']['added_ms'], 1)} | {streamed} | "
        f"{s['usd_per_million']:.5f} | {s['rps_per_dollar']:,.0f} |"
    )
lines += ["", "Ranges (min to max over rounds):", ""]
for gw in GWS:
    s = summary[gw]
    lines.append(f"- {s['label']}: added p50 c16 {cell(s['sweep']['16']['p50_added_ms'], 3)}, p99 c16 {cell(s['sweep']['16']['p99_added_ms'], 3)}, rps c64 {cell(s['sweep']['64']['rps'], 0)}")
    for key, v in s["sessions"].items():
        if v["error_sample"]:
            lines.append(f"  - {key}: {v['rounds_ok']}/{v['rounds']} rounds ok, sample error: {str(v['error_sample'])[:200]}")
    if s["stream"]["error_sample"]:
        lines.append(f"  - stream errors {s['stream']['errors']}, sample: {str(s['stream']['error_sample'])[:200]}")
(OUT / "summary.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
