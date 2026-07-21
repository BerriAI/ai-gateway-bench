"""Render the AIGatewayBench headline chart from measured results.

Two panels, lower is better:
  - p99 added latency (gateway p99 - direct-to-mock p99, same endpoint)
  - peak RSS (process memory footprint)

Data is read from results/, never hard-coded. Run the bench first, then:
    python analyze/make_chart.py
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = Path(__file__).resolve().parent.parent / "results"
OUT = Path(__file__).resolve().parent / "overhead_comparison.png"

LITELLM_COLOR = "#00b34a"
OTHER_COLOR = "#3a3f4b"

# gateway key in overhead_summary.json -> (display label, memory summary file)
GATEWAYS = {
    "litellm-rust": "LiteLLM (Rust)",
    "bifrost": "Bifrost",
    "portkey": "Portkey",
    "litellm-python": "LiteLLM (Python v1)",
    "agentgateway": "Agentgateway",
}


@dataclass(frozen=True)
class Row:
    label: str
    added_p99_ms: float
    peak_rss_mb: float
    is_litellm: bool


def _load() -> list[Row]:
    with (RESULTS / "overhead_comparison.csv").open(newline="") as file:
        overhead = {item["gateway"]: item for item in csv.DictReader(file)}
    rows = []
    for key, label in GATEWAYS.items():
        rows.append(
            Row(
                label=label,
                added_p99_ms=float(overhead[key]["p99_added_latency_ms"]),
                peak_rss_mb=float(overhead[key]["peak_rss_mb"]),
                is_litellm=(key == "litellm-rust"),
            )
        )
    return rows


def _panel(
    ax, rows: list[Row], values, unit: str, title: str, log: bool = False, decimals: int = 1
) -> None:
    order = sorted(range(len(rows)), key=lambda i: values[i], reverse=True)
    labels = [rows[i].label for i in order]
    vals = [values[i] for i in order]
    colors = [LITELLM_COLOR if rows[i].is_litellm else OTHER_COLOR for i in order]
    floor = min(vals) / 3.0 if log else 0.0
    bars = ax.barh(range(len(rows)), vals, left=floor if log else 0, color=colors, height=0.62)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels, fontsize=11)
    ax.set_title(title, fontsize=12, fontweight="bold", loc="left", pad=10)
    ax.set_xlabel(f"{unit}  (lower is better)", fontsize=9, color="#666")
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(left=False)
    if log:
        ax.set_xscale("log")
        ax.set_xlim(floor, max(vals) * 2.2)
    else:
        ax.set_xlim(0, max(vals) * 1.18)
    for bar, value in zip(bars, vals):
        x = bar.get_width() + floor if log else bar.get_width()
        offset = x * 0.12 if log else max(vals) * 0.02
        ax.text(x + offset, bar.get_y() + bar.get_height() / 2,
                f"{value:.{decimals}f}", va="center", fontsize=10,
                fontweight="bold", color="#222")


def main() -> None:
    rows = _load()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 3.6))
    _panel(ax1, rows, [r.added_p99_ms for r in rows], "p99 added latency (ms, log scale)",
           "Gateway overhead — p99 added latency", log=True, decimals=2)
    _panel(ax2, rows, [r.peak_rss_mb for r in rows], "peak RSS (MB)",
           "Deploy cost — peak memory")
    fig.suptitle("AIGatewayBench: overhead vs a local deterministic mock", fontsize=14,
                 fontweight="bold", x=0.02, ha="left")
    fig.text(0.02, -0.02,
             "n=5000 per endpoint on one host. Overhead = gateway p99 - direct-to-mock p99 on the same "
             "endpoint. All requests use the Anthropic Messages body; Bifrost uses its native "
             "/anthropic/v1/messages integration prefix.",
             fontsize=7, color="#888", ha="left")
    fig.tight_layout(rect=(0, 0.04, 1, 0.93))
    fig.savefig(OUT, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
