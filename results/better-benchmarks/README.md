# Better Benchmarks: chart data

The data behind each chart in the "Better Benchmarks" post. One CSV per chart.
[`analyze/better_benchmarks/make_charts.py`](../../analyze/better_benchmarks/make_charts.py) draws
every chart from these files, and `render.js` next to it turns them into PNGs.

| CSV | Chart | What it is | How to reproduce |
|---|---|---|---|
| `sweep.csv` | `sweep-linear.png` | 10 to 100 rps, 5 min each, full features (Portkey has none). Raw overhead is observed time minus the mock's planned time. Gateway overhead also subtracts the direct-to-mock floor at the same rate and percentile. The floor came from a separate run; runs drift about ±0.7 ms. Python at 75 and 100 rps was overloaded (see `ok_pct`). | [`scenarios/full_feature_sweep`](../../scenarios/full_feature_sweep) and [`scenarios/direct_floor`](../../scenarios/direct_floor) |
| `headline.csv` | `headline.png` | The 50 rps rows of `sweep.csv`, plus OpenRouter from a separate hosted test (Azure eastus, 8 rps, 20 min; p50 minus a direct call to the same mock, p99 is its own p99 overhead). | As above for the self-hosted gateways. The OpenRouter test is not in this repo yet. |
| `big-bodies.csv` | `big-bodies.png` | 870k to 1M token requests (~4 MB), every request streamed, basic features, 3 min per rate. No direct-to-mock floor at this size, so first byte includes moving 4 MB over two hops. | [`scenarios/stream_1m`](../../scenarios/stream_1m) |
| `request-bars.csv` | `request-bars.png` | One non-streamed `/v1/chat/completions` request on a local machine, one pinned core per gateway, all callbacks on. Python phases are VizTracer times scaled to the untraced client time of the same request; Rust phases come from span timers in the gateway. | Not in this repo yet (needs a tracing patch to the gateway). |
| `memory.csv` | `memory.png` | Heap held per open stream with 500k-token (2.45 MB) streamed requests: memray (Python), pprof (Bifrost), heaptrack (Rust), one core per gateway. Rust + S3 has the `s3_v2` request log on, which keeps one copy of the body for the stream's life. | Not in this repo yet. |

Hardware for the sweep and the 1M runs: Kubernetes on AWS, m7i.4xlarge nodes, one pod per gateway
with 4 dedicated cores and 16 GiB. Gateways: LiteLLM Rust and Python from
[BerriAI/litellm@5990e10f](https://github.com/BerriAI/litellm/commit/5990e10ff8adda1a69bec9c7412083daf8ed4b9b)
(Python with 4 workers), Bifrost and Portkey as pinned in
[`lanes/k8s`](../../lanes/k8s). Single run of each configuration.
