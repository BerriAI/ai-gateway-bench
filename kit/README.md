# Load-test kit

Out-of-process load testing for AI gateways: a mock LLM, an open-loop load generator and the
gateway each run in their own container, locally or on separate cloud machines. It drives the
LiteLLM Rust gateway (`litellm-gateway`, `litellm-rust/crates/gateway` in
[BerriAI/litellm](https://github.com/BerriAI/litellm)), the LiteLLM Python proxy, Bifrost and
Portkey with the same requests. The Kubernetes lane in [`../lanes/k8s`](../lanes/k8s) runs this kit
for the published comparisons.

| Tool | What it does |
| --- | --- |
| `mockllm` | Fake OpenAI chat completions, Anthropic messages and Bedrock invoke upstream. Streams or not, and takes its timing from a directive in the prompt (`[[mock ttft_ms=800 dur_ms=300000 out=2000]]`). Counts hits per deployment (`/d/<id>/` path prefix) at `GET /stats`; `-faults "chat-0-3=429:1.0"` makes chosen deployments fail. |
| `loadgen` | Open-loop arrivals from a workload file, Poisson or evenly spaced (`"arrivals": "constant"`): weighted request classes with their own endpoint, stream share, context size (10k to 1M tokens), time to first token and total time. Writes one JSON line per request, with `lag_ms`, how late it left against its planned arrival (when that grows, the generator is the limit). Shard across machines with `-shard i -shards n`. `-model-headers` adds per-model-group headers (Portkey's routing config). |
| `genconfig` | Gateway `config.yaml` with many model groups and deployments pointing at the mocks, plus `keys.txt` and `keys.sql` (rows for `LiteLLM_VerificationToken`). `-key-seed` makes every machine generate the same keys. The `bifrost` key set is written as a Bifrost config; see [Bifrost](#bifrost). The `portkey` key set is written as Portkey routing headers; see [Portkey](#portkey). |
| `summarize` | Merges result files and prints proxy overhead (observed minus planned time) by stage, class and context size. |
| `procmon` | Samples the gateway process's CPU, RSS, fds and TCP connections from `/proc`. |
| `verify` | Checks what each gateway recorded for a run against what loadgen sent: spend logs, key/team/user/member spend, daily tables, Prometheus counters, S3 request logs and the headers the provider received. See [Full-feature runs](#full-feature-runs). |
| `s3stub` | In-memory path-style S3 (PUT, GET, ListObjectsV2) for the `s3_v2` logs of a full-feature run. Objects are stored compressed and listed from a sorted index, so millions of logs fit. |

Workloads live in `workloads/`: `smoke`, `realistic-ramp`, `long-streams`, `large-context`, `stream-1m` (all streamed, near-1M-token bodies),
`key-churn` and `soak`. `sustained-100`, `sustained-250`, `sustained-500` and `sustained-1000` hold one rate for
15 minutes with evenly spaced arrivals and smoke's request mix over every model group (`sweep-10`, `-25`, `-50`,
`-75` and `-100` the same for 5 minutes each, for a low-rate sweep);
`sustained-check` runs the same mix at 100, 500 and 1,000 rps for 2 minutes each, to check that the
client, mock and stack hold 1,000 rps before the long runs.

## Build the images

```sh
export LITELLM_SRC=<a clone of BerriAI/litellm>
scripts/build-images.sh            # fat LTO gateway, what ships
FAST=1 scripts/build-images.sh     # no LTO, several times faster to build, for iterating
PUSH=1 REGISTRY=<registry>/<prefix> scripts/build-images.sh
```

The gateway is built from `LITELLM_SHA` in `LITELLM_SRC` (default `5990e10f`, the commit the
published numbers used). Images are tagged `gateway-<sha>` so every result maps to a commit.

## Run locally (correctness, not perf numbers)

```sh
mkdir -p out
docker run --rm -v "$PWD/out:/work/out" litellm-loadtest:<tag> genconfig -out out \
  -mocks http://mockllm:9000 -keys 100
export GATEWAY_IMAGE=litellm-rust-gateway:<tag> TOOLS_IMAGE=litellm-loadtest:<tag>
docker compose up -d gateway mockllm procmon
docker compose run --rm loadgen loadgen -workload /workloads/smoke.json \
  -target http://gateway:4000 -key sk-loadtest-master -out out/smoke.jsonl
docker compose run --rm loadgen summarize out/smoke.jsonl
docker compose logs procmon
```

Or without Docker: `go build -o bin/ ./cmd/...` and run the binaries against
`cargo run --release -p litellm-gateway` (in `litellm-rust/` of the LiteLLM checkout) with
`LITELLM_CONFIG=out/config.yaml`.

## Virtual keys

The gateway accepts the master key and the virtual keys in `LiteLLM_VerificationToken`, read from
`general_settings.database_url` or `DATABASE_URL`. Load `keys.sql` into a database the proxy's
migrations have created, start the gateway with `DATABASE_URL` set, and drop `-key` so loadgen
spreads requests over `out/keys.txt`:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f out/keys.sql
DATABASE_URL=... LITELLM_CONFIG=out/config.yaml litellm-gateway &
loadgen -workload workloads/key-churn.json -target http://127.0.0.1:4000 -messages-auth x-api-key
```

`-messages-auth x-api-key` sends the key the way the Anthropic SDK does; the gateway also reads
`Authorization: Bearer` and Azure's `api-key`. Keys are cached for 60 s (unknown keys for 5 s), so
`key-churn` exercises the miss path and other workloads mostly hit the cache. With `create_postgres`
in Terraform the stack migrates the database, loads `keys.sql` and passes `DATABASE_URL` to the
gateway; leave `KEY` unset for `cloud-run.sh`. `-key sk-loadtest-master` still works and needs no
database.

## Run in AWS

`terraform/aws` creates one VPC with a cluster placement group holding the gateway machine(s),
mock machines and load generator machines, plus ECR repositories and, optionally, an RDS Postgres
for virtual keys. With `gateway_count > 1` the generators go through an internal NLB.

```sh
cd terraform/aws
cp terraform.tfvars.example terraform.tfvars   # set ssh key, your IP, image_tag
terraform init && terraform apply
# push images to the ECR repos the stack created (machines retry pulls for an hour)
aws ecr get-login-password | docker login --username AWS --password-stdin "$(terraform output -raw ecr_registry)"
cd ../.. && PUSH=1 REGISTRY="$(terraform -chdir=terraform/aws output -raw ecr_registry)/litellm-loadtest" \
  TAG=<image_tag> scripts/build-images.sh

KEY=sk-loadtest-master scripts/cloud-run.sh smoke
KEY=sk-loadtest-master scripts/cloud-run.sh realistic-ramp
terraform -chdir=terraform/aws destroy
```

`cloud-run.sh` runs a shard on every generator, copies results to `results/<run>/`, collects
gateway samples and mock hit counts, and prints the summary.

## Reading the results

- **First-byte overhead**: time to the first SSE event (streams) or first body byte (non-streaming)
  minus the planned time. This is what the proxy adds to perceived latency.
- **Total overhead**: total time minus the planned duration.
- **Max stream gap**: the longest pause between two SSE events. Compare it with the planned chunk
  interval (duration / chunks); a gap far above it means the proxy held data back.
- **Shed** (in the loadgen log): arrivals dropped because the generator hit `max_in_flight`. Any
  shedding means the generator, not the gateway, was the limit, and the run is invalid.

## Gateway metrics and drain

- `GET /metrics` on the gateway port serves Prometheus text: request count, latency and time to
  first byte by route, model group and status, `litellm_in_flight_requests`, and
  `litellm_deployment_failure_responses_total` by deployment `api_base`. Scrape it before and
  after a run to cross-check the loadgen numbers from the gateway's side. Like the Python proxy
  it needs a key by default (`curl -H "authorization: Bearer sk-loadtest-master" .../metrics`).
  Set `litellm_settings.require_auth_for_metrics_endpoint: false` to make it public
- `GET /health/readiness` turns 503 on SIGTERM. The listener closes after
  `LITELLM_UNREADY_DELAY_SECONDS` (default 0), then open requests and streams get up to
  `LITELLM_DRAIN_SECONDS` (default 900, the NLB deregistration delay) to finish

## Full-feature runs

`genconfig -features full -key-sets python,rust` turns on everything both gateways do per request,
with limits no load-test rate reaches, so every check runs and nothing is refused:

- every key has a user and a team (with a membership budget), and keys, users, teams and members
  carry rpm, tpm and budgets; keys carry `max_parallel_requests`; deployments carry rpm and tpm
- callbacks `dynamic_rate_limiter_v3`, `prometheus` and `s3_v2` (S3 settings from `LOADTEST_S3_*`),
  plus the Rust gateway's four request hooks under `litellm_settings.hooks` and their Python
  stand-ins in `custom_callbacks.py`, written beside the config
- `headers-<set>.txt`: the request headers the hooks act on, one loadgen `-header` per line

Each key set (one per gateway) has its own keys, users and teams, so both gateways can share one
Postgres and one mock and still be told apart. Every request names its input token count to the
mock (`in=`), so both sides must record the same tokens. Set `DEFAULT_S3_FLUSH_INTERVAL_SECONDS` on
both gateways to control the S3 batch interval. After the run and the spend flush:

```sh
verify expect -out col/expected-rust.json res/rust-*.jsonl
verify sql -set rust | psql "$DATABASE_URL" -At > col/db-rust.json   # same for python
verify check -dir col -s3 http://s3stub:9100/logs -mock http://mockllm:9000 \
  -side python=python-gateway:4000,python -side rust=rust-gateway:4000,rust -report report.md
```

`check` scrapes `/metrics` on every address the host name resolves to (one per replica) and
exits 1 when any check fails. Across sides it compares each deployment's fitted input and output
price rather than total spend, since routing spreads requests over differently priced
deployments, and it lists the metrics, labels and S3 fields one side records and the other lacks.
mockllm needs `-count-headers x-request-origin,x-trace-id,x-caller-team,x-cost-center,x-litellm-spend-logs-metadata`.

## Bifrost

A key set named `bifrost` (`-key-sets python,rust,bifrost`) is written as a config for
[Bifrost](https://github.com/maximhq/bifrost)'s open-source build instead of key rows:
`bifrost-config.json` (load it as the gateway's `config.json`), `bifrost-pricing.json` (mount it at
`-bifrost-pricing-file`, default `/config/bifrost-pricing.json`), `keys-bifrost.txt` and, with
`-features full`, `headers-bifrost.txt`. Each deployment is a custom provider pointing at the mock's
`/d/<id>`, each model group a routing rule with equal-weight targets, and each key a virtual key.
Bifrost reads its config and log store from `BIFROST_PG_{HOST,PORT,USER,PASSWORD,DB}` and, with full
features, S3 from `LOADTEST_S3_*` (objects under `bifrost/`).

Full features map onto what open-source Bifrost has: key, team and customer budgets and rate limits
(the customer stands in for LiteLLM's user; users and team members are enterprise-only), model
configs for deployment rpm and tpm, the telemetry plugin's `/metrics`, the logs store with S3
payloads, `header_filter_config` for forward_headers and strip_headers, and `logging_headers` for
spend_metadata. provider_metadata (adding the key's team as a header) has no counterpart.
Bifrost keeps rate limits, budgets and counters in each replica's memory, with no shared store, so
it has no cross-replica state at all.

Bifrost's Bedrock provider uses Converse over HTTPS only, so run mockllm with `-tls-addr :9443`
(a self-signed listener; genconfig points Bedrock at `-bifrost-bedrock-tls` and skips verification).
The mock echoes the request's model in responses, since Bifrost prices by the response's model.
Bifrost serves Anthropic messages at `/anthropic/v1/messages`:

```sh
loadgen -workload workloads/smoke.json -target http://bifrost:8080 -keys out/keys-bifrost.txt \
  -messages-path /anthropic/v1/messages -messages-auth x-api-key \
  -header '<each line of out/headers-bifrost.txt>' ...
sleep 15   # Bifrost writes budget usage to Postgres every 10 s
verify sql -set bifrost | psql "$BIFROST_DATABASE_URL" -At > col/db-bifrost.json
verify check ... -side bifrost=bifrost:8080,bifrost
```

`verify` reads a side named `bifrost` in Bifrost's schema (the `logs` and `governance_budgets`
tables, `bifrost_*` metrics, its S3 log objects) and reports what Bifrost has no counterpart for as
n/a. With three sides it compares each pair's fitted deployment prices; metric and S3 field lists
are only compared between the LiteLLM sides, since Bifrost's schema differs throughout.

## Portkey

A key set named `portkey` (`-key-sets ,portkey`; with `-features full` it is unchanged, since Portkey has none of those features) is written as routing for the
[open-source Portkey gateway](https://github.com/Portkey-AI/gateway) instead of key rows. That
gateway keeps no model list, keys or database: every request carries its routing in an
`x-portkey-config` header. So genconfig writes `portkey-headers.json`, one header per model group,
and `keys-portkey.txt`, keys of the same shape as the other sets that Portkey never checks (it has
no client auth). Each group's config load-balances evenly over the group's deployments (Portkey's
counterpart of simple-shuffle) with `retry.attempts` 2, and each target points at the mock's
`/d/<id>` with `custom_host` and `override_params.model`. Bedrock targets carry static AWS keys, so
requests are SigV4 signed; `custom_host` replaces the Bedrock URL, so they reach the mock over plain
HTTP, and Portkey sends Anthropic models' messages to Converse. Portkey has no limits, budgets, logs
or metrics, so `-features full` with the `portkey` set is refused.

Portkey refuses a `custom_host` on a private or `.local` name unless `TRUSTED_CUSTOM_HOSTS` lists
it, so start it with that set to the mock's host name. loadgen sends each request's header with
`-model-headers`, and fails at start when a model the workload can pick has none:

```sh
TRUSTED_CUSTOM_HOSTS=mockllm node build/start-server.js --headless --port=8787
loadgen -workload workloads/smoke.json -target http://portkey:8787 -keys out/keys-portkey.txt \
  -model-headers out/portkey-headers.json ...
```

## Bedrock deployments

`genconfig` serves half of each messages group through Bedrock invoke by default
(`-bedrock-fraction 0.5`, `bedrock_fraction` in Terraform; 0 for Anthropic only). Those deployments
carry static dummy AWS keys, so every request is SigV4-signed, and mockllm answers
`/model/<id>/invoke` and `/invoke-with-response-stream` with AWS event-stream framing that the
gateway re-encodes as Anthropic SSE. Bedrock hits show up as `bedrock-<g>-<i>` in the mock's `/stats`.
