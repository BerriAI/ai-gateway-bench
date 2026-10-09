"""Hosted real-model chart: TTFT per arm and p50 delta vs direct Anthropic.

Reads results/hosted_ttft.csv (built by tools/build_hosted_csv.py) and writes
analyze/hosted_ttft.png.
"""

from __future__ import annotations

import csv
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

RESULTS = Path(__file__).resolve().parent.parent / "results"
OUT = Path(__file__).resolve().parent
LITELLM_COLOR = "#00b34a"
OTHER_COLOR = "#3a3f4b"
DIRECT_COLOR = "#8a8f9a"

ARMS = ["direct", "litellm-rust", "litellm-python", "openrouter"]
LABELS = {
    "direct": "Direct Anthropic",
    "litellm-rust": "LiteLLM (Rust)",
    "litellm-python": "LiteLLM (Python v1)",
    "openrouter": "OpenRouter",
}


def _colors(keys: list[str]) -> list[str]:
    return [
        LITELLM_COLOR if key == "litellm-rust" else DIRECT_COLOR if key == "direct" else OTHER_COLOR
        for key in keys
    ]


def _csv(name: str) -> list[dict[str, str]]:
    with (RESULTS / name).open(newline="") as file:
        return list(csv.DictReader(file))


def hosted_ttft_chart() -> None:
    data = {row["gateway"]: row for row in _csv("hosted_ttft.csv")}
    keys = ARMS
    labels = [LABELS[key] for key in keys]
    colors = _colors(keys)
    x = list(range(len(keys)))

    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.8))

    width = 0.35
    p50 = [float(data[key]["ttft_p50_ms"]) for key in keys]
    p90 = [float(data[key]["ttft_p90_ms"]) for key in keys]
    left.bar([v - width / 2 for v in x], p50, width=width, color=colors, alpha=0.95)
    left.bar([v + width / 2 for v in x], p90, width=width, color=colors, alpha=0.5)
    left.set_xticks(x, labels)
    left.set_ylabel("TTFT (ms)")
    left.set_title("Streaming TTFT per arm", loc="left", fontweight="bold")
    left.tick_params(axis="x", rotation=20)
    left.grid(axis="y", alpha=0.2)
    left.legend(
        handles=[
            mpatches.Patch(facecolor="#222", alpha=0.95, label="p50"),
            mpatches.Patch(facecolor="#222", alpha=0.5, label="p90"),
        ],
        frameon=False,
    )

    delta_keys = [key for key in keys if key != "direct"]
    delta = [float(data[key]["p50_delta_vs_direct_ms"]) for key in delta_keys]
    lo = [float(data[key]["p50_delta_vs_direct_ms"]) - float(data[key]["p50_ci_lo_vs_direct_ms"]) for key in delta_keys]
    hi = [float(data[key]["p50_ci_hi_vs_direct_ms"]) - float(data[key]["p50_delta_vs_direct_ms"]) for key in delta_keys]
    dx = list(range(len(delta_keys)))
    right.bar(dx, delta, width=0.55, color=_colors(delta_keys))
    right.errorbar(dx, delta, yerr=[lo, hi], fmt="none", ecolor="#222", elinewidth=1.2, capsize=4)
    right.axhline(0, color="#999", linewidth=0.8)
    right.set_xticks(dx, [LABELS[key] for key in delta_keys])
    right.set_ylabel("p50 delta vs direct (ms)")
    right.set_title("Added TTFT over direct Anthropic", loc="left", fontweight="bold")
    right.tick_params(axis="x", rotation=20)
    right.grid(axis="y", alpha=0.2)

    n_per_arm = int(data["direct"]["requests"])
    total = sum(int(row["requests"]) for row in data.values())
    python_new_conns = int(data["litellm-python"]["new_connections"])
    footnote = (
        f"n={n_per_arm} per arm, {total} requests shuffled across arms, warm persistent clients. "
        "Streaming /v1/messages, claude-haiku-4-5. TTFT = first text_delta event. "
        "Error bars: bootstrap 95% CI. "
        f"Python v1 opened {python_new_conns} new connections (gateway-side closes); "
        "that cost is included in its numbers."
    )
    fig.text(0.01, 0.01, "\n".join(textwrap.wrap(footnote, 140)), fontsize=7, color="#777")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(OUT / "hosted_ttft.png", dpi=160, bbox_inches="tight")


if __name__ == "__main__":
    hosted_ttft_chart()
