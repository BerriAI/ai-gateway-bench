"""Charts for the "Better Benchmarks" post, as SVG, from the CSVs in results/better-benchmarks/.

    python analyze/better_benchmarks/make_charts.py
    node analyze/better_benchmarks/render.js      # SVG to PNG (Playwright, IBM Plex Sans / JetBrains Mono)
"""

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / "results" / "better-benchmarks"

FG = "#18212B"
MUTED = "#5B6470"
GRID = "#D9D4C9"
LINE_GRID = "#E2DDD2"
BG = "#FFFFFF"
PANEL = "#F5F3EE"
RUST = "#2B44D6"
OTHER = "#9AA3B2"
BODY = "#B4501F"
SANS = "IBM Plex Sans, Arial, sans-serif"
MONO = "JetBrains Mono, monospace"
LINE_COLORS = {"LiteLLM Rust": "#2B44D6", "Bifrost": "#E8894F", "Portkey": "#7A8597", "LiteLLM Python": "#18212B"}


def rows(name):
    with open(DATA / name, newline="") as f:
        return list(csv.DictReader(f))


def num(s):
    return float(s) if s not in ("", None) else None


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")


def T(x, y, s, size=16, fill=FG, w=400, anchor="start", font=SANS):
    return (
        f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{w}" '
        f'fill="{fill}" text-anchor="{anchor}">{esc(s)}</text>'
    )


def svg_open(w, h):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">', f'<rect width="{w}" height="{h}" fill="{BG}"/>']


def save(name, o):
    o.append("</svg>")
    (HERE / f"{name}.svg").write_text("\n".join(o))


def headline():
    data = rows("headline.csv")
    labels = {"LiteLLM Rust": "LiteLLM\nRust", "LiteLLM Python": "LiteLLM\nPython", "OpenRouter": "OpenRouter*"}
    names = [labels.get(r["gateway"], r["gateway"]) for r in data]

    def ms(v):
        return f"{v:g} ms" if v < 1000 else f"{v / 1000:.2f} s"

    panels = [
        ("Latency p50", "added by the gateway", "gateway_overhead_p50_ms", ms),
        ("Latency p99", "added by the gateway", "gateway_overhead_p99_ms", ms),
        ("CPU", "cores used", "cpu_cores_mean", lambda v: f"{v:g}"),
        ("Memory", "peak", "peak_memory_mib", lambda v: f"{v:,.0f} MiB" if v < 1024 else f"{v / 1024:.1f} GiB"),
    ]
    PW, PH, GAP = 430, 520, 24
    W = PW * 4 + GAP * 3
    H = PH + 70
    o = svg_open(W, H)
    for i, (title, sub, col, fmt) in enumerate(panels):
        vals = [num(r[col]) for r in data]
        x0 = i * (PW + GAP)
        o.append(f'<rect x="{x0}" y="0" width="{PW}" height="{PH}" rx="14" fill="{PANEL}"/>')
        o.append(T(x0 + 24, 44, title, 26, w=600))
        o.append(T(x0 + 24, 72, sub + ", lower is better", 16, MUTED))
        shown = sorted(v for v in vals if v is not None)
        # Latency bars more than 10x the smallest are capped (drawn full height with a point) so the
        # rest stay readable; the uncapped bars then use 40% of the height.
        cap = None
        big = [v for v in shown if v > 10 * shown[0]]
        if title.startswith("Latency") and big and len(big) < len(shown) - 1:
            cap = min(big)
        unc = [v for v in shown if cap is None or v < cap]
        frac = 0.4 if cap is not None else 1.0
        top, base = 150, PH - 80
        hmax = base - top
        n = len(names)
        cw = (PW - 48) / n
        bw = cw * 0.62
        o.append(f'<line x1="{x0 + 24}" x2="{x0 + PW - 24}" y1="{base}" y2="{base}" stroke="{GRID}" stroke-width="1.5"/>')
        for j, v in enumerate(vals):
            cx = x0 + 24 + cw * j + cw / 2
            first = j == 0
            color = RUST if first else OTHER
            for k, line in enumerate(names[j].split("\n")):
                o.append(T(cx, base + 24 + k * 19, line, 15, RUST if first else MUTED, 600 if first else 400, "middle"))
            if v is None:
                o.append(f'<rect x="{cx - bw / 2}" y="{base - 48}" width="{bw}" height="48" rx="4" fill="none" stroke="{GRID}" stroke-dasharray="4 3"/>')
                o.append(T(cx, base - 20, "n/a", 14, MUTED, anchor="middle"))
                continue
            capped = cap is not None and v >= cap
            h = hmax if capped else max(3, hmax * frac * v / max(unc))
            y = base - h
            if capped:
                o.append(f'<path d="M{cx - bw / 2},{base} V{y + 14} L{cx},{y} L{cx + bw / 2},{y + 14} V{base} Z" fill="{color}"/>')
            else:
                o.append(f'<rect x="{cx - bw / 2}" y="{y}" width="{bw}" height="{h}" rx="4" fill="{color}"/>')
            o.append(T(cx, y - 10, fmt(v), 17, RUST if first else FG, 600, "middle"))
    o.append(T(0, PH + 30, "50 rps for 5 min, every feature on (Portkey has none). Every gateway served 100% of requests. Same pod size: 4 cores, 16 GiB. Latency is minus a direct-to-mock run.", 16, MUTED))
    o.append(T(0, PH + 56, "*OpenRouter: separate test at 8 rps from Azure eastus, overhead vs. a direct call to the same mock. Capped bars are not to scale. CPU and memory n/a (hosted).", 16, MUTED))
    save("headline", o)


def request_bars():
    data = rows("request-bars.csv")
    cats = [
        ("Auth", "auth_ms", "#2B44D6"),
        ("Limits and hooks", "limits_and_hooks_ms", "#E8894F"),
        ("Build request, call provider", "build_request_and_call_provider_ms", "#7A8597"),
        ("Everything else", "everything_else_ms", "#D9D4C9"),
    ]
    W, LW, AX = 1600, 230, 60
    pw = W - LW - 120
    rh, gap, top = 38, 22, 70
    H = top + len(data) * (rh + gap) + 80
    o = svg_open(W, H)
    lx = 0
    for name, _, c in cats:
        o.append(f'<rect x="{lx}" y="14" width="16" height="16" rx="3" fill="{c}"/>')
        o.append(T(lx + 24, 28, name, 16, FG))
        lx += 24 + len(name) * 9 + 36
    bt = top + len(data) * (rh + gap) - gap
    for t in range(0, AX + 1, 10):
        x = LW + pw * t / AX
        o.append(f'<line x1="{x}" x2="{x}" y1="{top - 8}" y2="{bt + 6}" stroke="{GRID}" stroke-width="1"/>')
        o.append(T(x, bt + 28, f"{t} ms", 14, MUTED, anchor="middle", font=MONO))
    for i, r in enumerate(data):
        y = top + i * (rh + gap)
        label = r["request"].removeprefix("LiteLLM ")
        o.append(T(0, y + rh / 2 + 6, label, 17, FG, 600 if label.startswith("Rust") else 400))
        x = LW
        for _, col, c in cats:
            w = pw * float(r[col]) / AX
            o.append(f'<rect x="{x:.2f}" y="{y}" width="{max(w, 0):.2f}" height="{rh}" fill="{c}"/>')
            x += w
        o.append(T(x + 10, y + rh / 2 + 6, f"{float(r['total_ms']):.1f} ms", 16, FG, 600, font=MONO))
    o.append(T(0, H - 8, "One non-streamed request, arrival to last response byte. Python phases scaled from a traced run.", 15, MUTED))
    save("request-bars", o)


def memory():
    data = rows("memory.csv")
    order = []
    by = {}
    for r in data:
        g = r["gateway"]
        if g not in by:
            order.append(g)
            by[g] = {"copies": [], "buf": 0.0}
        mb = float(r["mb_per_open_stream"])
        if r["kind"] == "copy of the request body":
            label = r["component"].replace("body kept for the S3 log", "kept for the S3 log")
            # A copy too small to carry its own label joins the one before it.
            if mb < 0.6 and by[g]["copies"]:
                prev_mb, prev_label = by[g]["copies"][-1]
                by[g]["copies"][-1] = (prev_mb + mb, prev_label.split(" (")[0] + " + " + label.split(" ")[0])
            else:
                by[g]["copies"].append((mb, label))
        else:
            by[g]["buf"] += mb
    W, LW, XM = 1600, 260, 12
    pw = W - LW - 140
    rh, g3, top = 40, 48, 70
    H = top + len(order) * (rh + g3) + 90
    o = svg_open(W, H)
    o.append(f'<rect x="0" y="14" width="16" height="16" rx="3" fill="{BODY}"/>')
    o.append(T(24, 28, "Copy of the request body", 16))
    o.append(f'<rect x="270" y="14" width="16" height="16" rx="3" fill="{RUST}"/>')
    o.append(T(294, 28, "Buffers and per-call state", 16))
    bt = top + len(order) * (rh + g3) - g3 + 24
    for t in range(0, XM + 1, 2):
        x = LW + pw * t / XM
        o.append(f'<line x1="{x}" x2="{x}" y1="{top - 8}" y2="{bt + 6}" stroke="{GRID}"/>')
        o.append(T(x, bt + 28, f"{t} MB", 14, MUTED, anchor="middle", font=MONO))
    for i, name in enumerate(order):
        y = top + i * (rh + g3)
        o.append(T(0, y + rh / 2 + 6, name, 17, FG, 600 if "Rust" in name else 400))
        x = LW
        for mb, label in by[name]["copies"]:
            w = pw * mb / XM
            o.append(f'<rect x="{x:.2f}" y="{y}" width="{w - 2:.2f}" height="{rh}" fill="{BODY}"/>')
            o.append(T(x + 2, y + rh + 18, label, 13, MUTED))
            x += w
        buf = by[name]["buf"]
        if buf:
            w = max(pw * buf / XM, 3)
            o.append(f'<rect x="{x:.2f}" y="{y}" width="{w:.2f}" height="{rh}" fill="{RUST}"/>')
            x += w
        tot = sum(mb for mb, _ in by[name]["copies"]) + buf
        o.append(T(x + 10, y + rh / 2 + 6, (f"{tot:.2f}" if tot < 1 else f"{tot:.1f}") + " MB", 16, FG, 600, font=MONO))
    o.append(T(0, H - 8, "Heap held per open stream, 500k-token request (2.45 MB body).", 15, MUTED))
    save("memory", o)


def line_panels(name, panels, xs, xmin, xmax, PW, PH, cols, L, Rm, Tp, Bt, GAP, VG, note, hollow=None):
    nrows = (len(panels) + cols - 1) // cols
    W = PW * cols + GAP * (cols - 1)
    H = PH * nrows + VG * (nrows - 1) + 60
    o = svg_open(W, H)
    for i, (title, series, ymax, step, fmt) in enumerate(panels):
        x0 = (i % cols) * (PW + GAP)
        y0 = (i // cols) * (PH + VG)
        pw = PW - L - Rm
        ph = PH - Tp - Bt

        def X(r):
            return x0 + L + pw * (r - xmin) / (xmax - xmin)

        def Y(v):
            return y0 + Tp + ph * (1 - v / ymax)

        o.append(T(x0, y0 + 26, title, 22, w=600))
        v = 0
        while v <= ymax:
            o.append(f'<line x1="{x0 + L}" x2="{x0 + PW - Rm}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{LINE_GRID}"/>')
            o.append(T(x0 + L - 8, Y(v) + 5, fmt(v), 13, MUTED, anchor="end", font=MONO))
            v += step
        for r in xs:
            o.append(T(X(r), y0 + Tp + ph + 22, f"{r:g}", 13, MUTED, anchor="middle", font=MONO))
        o.append(T(x0 + L + pw / 2, y0 + Tp + ph + 44, "requests per second", 14, MUTED, anchor="middle"))
        for gw, pts in series.items():
            dash = ' stroke-dasharray="7 5"' if gw == "Portkey" else ""
            line = " ".join(f"{X(r):.1f},{Y(v):.1f}" for r, v in pts)
            o.append(f'<polyline points="{line}" fill="none" stroke="{LINE_COLORS[gw]}" stroke-width="{3 if "Rust" in gw else 2.2}"{dash}/>')
            for r, v in pts:
                label = hollow(gw, r) if hollow else None
                fill = BG if label else LINE_COLORS[gw]
                o.append(f'<circle cx="{X(r):.1f}" cy="{Y(v):.1f}" r="4.5" fill="{fill}" stroke="{LINE_COLORS[gw]}" stroke-width="2"/>')
                if label:
                    o.append(T(X(r), Y(v) + 22, label, 13, MUTED, anchor="middle"))
    lx = 0
    for gw in panels[-1][1]:
        dash = ' stroke-dasharray="7 5"' if gw == "Portkey" else ""
        o.append(f'<line x1="{lx}" x2="{lx + 28}" y1="{H - 24}" y2="{H - 24}" stroke="{LINE_COLORS[gw]}" stroke-width="3"{dash}/>')
        o.append(T(lx + 36, H - 19, gw + (" (no features)" if gw == "Portkey" else ""), 15))
        lx += 70 + len(gw) * 9 + (110 if gw == "Portkey" else 0)
    o.append(T(W, H - 19, note, 14, MUTED, anchor="end"))
    save(name, o)


def series(data, xcol, ycol, scale=1.0, skip=()):
    out = {}
    for r in data:
        if r["gateway"] in skip:
            continue
        out.setdefault(r["gateway"], []).append((float(r[xcol]), float(r[ycol]) * scale))
    return out


def sweep():
    data = rows("sweep.csv")
    order = ["LiteLLM Rust", "Bifrost", "Portkey", "LiteLLM Python"]
    data.sort(key=lambda r: (order.index(r["gateway"]), float(r["rps"])))
    ok = {(r["gateway"], float(r["rps"])): float(r["ok_pct"]) for r in data}

    def hollow(gw, r):
        pct = ok[(gw, r)]
        return f"{pct:.0f}% ok" if pct < 100 else None

    # Python's latency is far off these scales, so the latency panels leave it out.
    py = ("LiteLLM Python",)
    panels = [
        ("Overhead p50", series(data, "rps", "gateway_overhead_p50_ms", skip=py), 10, 2, lambda v: f"{v:g} ms"),
        ("Overhead p99", series(data, "rps", "gateway_overhead_p99_ms", skip=py), 10, 2, lambda v: f"{v:g} ms"),
        ("CPU cores used", series(data, "rps", "cpu_cores_mean"), 4, 1, lambda v: f"{v:g}"),
        ("Peak memory", series(data, "rps", "peak_memory_mib", 1 / 1024), 16, 4, lambda v: f"{v:g} GiB"),
    ]
    xs = sorted({float(r["rps"]) for r in data})
    line_panels("sweep-linear", panels, xs, min(xs), max(xs), 820, 440, 2, 80, 40, 60, 50, 60, 30,
                "Overhead minus a direct-to-mock run; Python latency off scale. 5 min per rate; 4 cores, 16 GiB per gateway.", hollow)


def big_bodies():
    data = rows("big-bodies.csv")
    gib = 1.0
    panels = [
        ("Time to first byte p50", series(data, "rps_nominal", "first_byte_overhead_p50_ms"), 200, 50, lambda v: f"{v:g} ms"),
        ("Time to first byte p99", series(data, "rps_nominal", "first_byte_overhead_p99_ms"), 200, 50, lambda v: f"{v:g} ms"),
        ("Peak memory", series(data, "rps_nominal", "peak_memory_gib", gib), 16, 4, lambda v: f"{v:g} GiB"),
    ]
    xs = sorted({float(r["rps_nominal"]) for r in data})
    line_panels("big-bodies", panels, xs, min(xs), max(xs), 560, 440, 3, 80, 30, 60, 50, 40, 0,
                "Basic features. Every request streamed, 870k to 1M tokens (~4 MB). 3 min per rate. First byte minus the mock's planned time.")


if __name__ == "__main__":
    headline()
    request_bars()
    memory()
    sweep()
    big_bodies()
