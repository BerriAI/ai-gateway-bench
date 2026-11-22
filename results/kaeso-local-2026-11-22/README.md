# Local lane with Kaeso, 2026-11-22

One run of this repository's local lane with [Kaeso](../../gateways/kaeso) added beside LiteLLM (Rust and Python),
Bifrost and Portkey. Each gateway ran in its newest version, on one machine, using this repository's mock and drivers
unchanged (`mock-upstream`, `load-probe`, `tools/run_http_bench.py`, `agents/session_runner.py`) at `44365ca`.

**Who ran it:** the author of Kaeso. Like the repository's own local results, these are single-host and vendor-run.
Run [`scripts/`](scripts) on your own machine before relying on them.

## Machine and setup

- DigitalOcean CPU-Optimized `c-4`: 4 dedicated vCPUs (Intel Xeon Platinum 8358, 2.6 GHz), 8 GB, Ubuntu 24.04, Docker.
- Each gateway ran in Docker on the host network, pinned to cores 2-3 (`--cpuset-cpus 2,3`). The mock, the drivers
  and Kaeso's PostgreSQL, Valkey and ClickHouse ran on cores 0-1 (`taskset -c 0,1`). Each gateway therefore has two
  cores, not the four of the Kubernetes lane.
- Three rounds. Each round runs the gateways in a new random order, and each gateway run is paired with a direct run
  of the same shape taken just before it. Each figure is the median over the rounds; the CSVs and JSON have min and max.
- CPU and RSS were sampled four times a second across the gateway container's whole process tree. The same sampling
  covered Kaeso's databases, and their numbers are reported separately.

| Gateway | Version | Checked on each request | Kept |
|---|---|---|---|
| Kaeso | 0.7.0 (`ghcr.io/logicplanes/kaeso-gateway@sha256:3d32fd78…`) | A project API key and the project's allowed models | Usage in PostgreSQL |
| Kaeso, request logs on | 0.7.0 | The same | Usage, and every request in ClickHouse |
| LiteLLM Rust | `main` at `d4e8f96` (unreleased), built with `rust:1.98.0` | Static master key | Nothing, no database |
| LiteLLM Python | 1.110.2 | Static master key | Nothing, no database |
| Bifrost | 2.6.0 | No key | Nothing, logging and stores off |
| Portkey | 1.15.2 | No key | Nothing, logging off |

Every image digest is in [`scripts/versions.txt`](scripts/versions.txt). LiteLLM Rust's Dockerfile at `d4e8f96` embeds
files from outside its build context, so `scripts/setup.sh` compiles the same commit in `rust:1.98.0` with the whole
repository mounted. A second build on a fresh machine produced the same binary sha256.

## Capacity: 64 clients, zero-delay mock

`MOCK_TTFT_MS=0 MOCK_ITL_MS=0`. The same driver measured the direct-to-mock ceiling each round, at about 28,000 req/s,
so the gateway was the limit in every run ([`capacity.csv`](capacity.csv)).

| Gateway | req/s | p50 / p99 (ms) | Gateway CPU per request | Peak RSS |
|---|---|---|---|---|
| Kaeso | 6,338 | 10.0 / 15.6 | 314 µs | 101 MB + 271 MB PostgreSQL and Valkey |
| Kaeso, request logs on | 5,324 | 11.8 / 20.6 | 373 µs | 450 MB + 1,163 MB with ClickHouse |
| LiteLLM Rust | 3,211 | 19.6 / 33.2 | 573 µs | 86 MB |
| Bifrost | 2,806 | 21.0 / 54.6 | 708 µs | 405 MB |
| Portkey | 779 | 79.9 / 118.3 | 1,449 µs | 293 MB |
| LiteLLM Python | 74 | 858.1 / 974.0 | 13,563 µs | 743 MB |

## Added latency: default mock (20 ms first token, 5 ms between 40 tokens)

p50 / p99 in ms, each run minus its paired direct run ([`latency_sweep.csv`](latency_sweep.csv)):

| Gateway | 1 client | 4 clients | 16 clients |
|---|---|---|---|
| Kaeso | +0.73 / +1.66 | +0.56 / +1.15 | +0.54 / +1.47 |
| Kaeso, request logs on | +0.77 / +2.22 | +0.60 / +1.90 | +0.62 / +1.45 |
| LiteLLM Rust | +0.87 / +0.98 | +0.76 / +1.68 | +0.69 / +2.26 |
| Bifrost | +1.10 / +1.32 | +0.85 / +2.41 | +0.71 / +4.08 |
| Portkey | +2.04 / +5.26 | +1.17 / +6.99 | +3.13 / +20.40 |
| LiteLLM Python | +16.0 / +17.8 | +50.9 / +60.5 | +188 / +216 |

There is no 64-client row. On this machine the mock and the drivers share two cores and saturate near 2,900 req/s, so
the direct baseline's tail rises and subtracting it understates every gateway's overhead. The 64-client values are in
the CSV, but the capacity pass is the measurement for that load.

## Streams and sessions

| Gateway | TTFT added p50 | Inter-chunk p99 (direct 7.5 ms) | Claude Code session added | Codex session added |
|---|---|---|---|---|
| Kaeso | +0.87 ms | 7.8 ms | +41.5 ms | +34.0 ms |
| Kaeso, request logs on | +0.94 ms | 8.1 ms | +66.0 ms | +65.5 ms |
| LiteLLM Rust | +0.96 ms | 40.4 ms | +41.0 ms | +44.2 ms |
| Bifrost | not comparable | 20.5 ms | +126.4 ms | +91.3 ms |
| Portkey | +27.5 ms | 8.6 ms | +91.4 ms | +104.2 ms |
| LiteLLM Python | +118.9 ms | 34.0 ms | +502.1 ms | +507.7 ms |

Bifrost sends `message_start` before the upstream answers, and `run_http_bench.py` counts that as the first token, so
its TTFT reads as negative. Every streamed session gave valid tool-call JSON through every gateway. No run of any
gateway recorded an error.

## Where Kaeso is not ahead

- LiteLLM Rust uses less memory: at most 86 MB with no database. Kaeso reached 164 MB over the whole run, plus
  271 MB for PostgreSQL and Valkey.
- With one client, LiteLLM Rust's p99 is lower (+0.98 ms against +1.66 ms), and the rounds' ranges don't overlap.
- Time to first token and the Claude Code session fall within each other's range.

## Reproduce

On a fresh Ubuntu 24.04 machine with four cores, as root:

```bash
bash results/kaeso-local-2026-11-22/scripts/setup.sh
cd /root
ai-gateway-bench/.venv/bin/python run.py
ai-gateway-bench/.venv/bin/python run_capacity.py
ai-gateway-bench/.venv/bin/python analyze.py out report
ai-gateway-bench/.venv/bin/python analyze_cap.py cap report
```

`setup.sh` installs Docker and builds this repository and LiteLLM Rust at the commits above. It then pulls every image
by digest and copies the scripts to `/root`. `run.py` takes about 90 minutes and `run_capacity.py` about 30. Absolute
numbers change between machines; compare gateways measured in the same run. A single-round rerun on another `c-4`
measured every gateway lower (Kaeso 5,287 and LiteLLM Rust 2,179 req/s). Kaeso led both runs, and Bifrost and
LiteLLM Rust swapped places within 3%.

The raw per-run JSON and gateway logs are published with the same scripts at
[kaeso.app/benchmarks](https://kaeso.app/benchmarks).
