# Kubernetes lane

This lane runs LiteLLM's Python proxy, the LiteLLM Rust gateway (`litellm-gateway`,
`litellm-rust/crates/gateway` in [BerriAI/litellm](https://github.com/BerriAI/litellm)), open-source
[Bifrost](https://github.com/maximhq/bifrost) and the open-source
[Portkey gateway](https://github.com/Portkey-AI/gateway) side by side in one Kubernetes namespace.
They all sit in front of the same mock upstream, Postgres and config. The load-test kit in
[`../../kit`](../../kit) replays a workload against each side in turn, with the same seed, so every
side gets the identical request sequence. The mock answers in a planned time, so whatever is left
over is the gateway.

It makes no real provider calls and needs no cloud account. Everything it needs is a Kubernetes
cluster and a container registry that cluster can pull from.

## What runs

1. **Images** (`bench.sh images`). From a LiteLLM checkout at `LITELLM_SHA`:
   - the Rust gateway from `litellm-rust/crates/gateway/Dockerfile`, with fat LTO by default (what ships)
   - the Python proxy from `gateway/Dockerfile`, whose native extension is the shipped build
   - the proxy's Prisma migrations from `migrations/Dockerfile`

   And from this repository, the kit's Go tools (`mockllm`, `loadgen`, `genconfig`, `verify`,
   `summarize`, `procmon`, `s3stub`) and `kit/workloads` (at `/workloads`) on `alpine/k8s`
   (`tools.Dockerfile`). Images already in the registry are skipped. Bifrost and Portkey are not
   built: the cluster pulls their own published images, pinned by digest in `bifrost.version` and
   `portkey.version`.
2. **Deploy** (`bench.sh deploy`, release `RELEASE` in `NAMESPACE`):
   - Postgres, and Redis with shared state on (`deps.yaml`)
   - the proxy migrations at the SHA, then 1,000 virtual keys with no limits (`jobs.yaml`)
   - `mockllm` and `s3stub`
   - one pod per gateway that `SIDES` selects, each with 4 dedicated cores and 16 GiB, requests
     equal to limits (`stack.yaml`)

   Every pod derives the same `config.yaml` and keys from `genconfig` with a fixed seed. That config
   has 10 chat groups and 10 messages groups of 4 deployments each, and half of each messages group
   runs on Bedrock invoke. Deploy prints the node of every pod, and the nodes' instance types when it
   may read nodes.
3. **Run** (`bench.sh run`). The load generator runs inside the cluster, as the Job
   `<release>-runner` (`runner.yaml`), so it is next to the gateways. `run.sh`:
   - sends a preflight of a few requests to every model group on every side with a virtual key. Any
     non-200 stops the run before load starts.
   - runs `loadgen` for each workload and round against each side in turn, with `-seed <round>`,
     and waits 30 s between runs so one side's closing connections and GC do not land in the next.
   - reads CPU and memory of the gateways, mock, s3stub and Postgres from `metrics.k8s.io` every
     10 s, and its own from its cgroup.
   - writes the comparison table and, with features full, runs `verify`.

   `bench.sh` follows the runner's log, then copies its results directory to `RESULTS_DIR`.
4. **Teardown** (`bench.sh teardown`). Saves the gateway, mock, s3stub, Postgres and Redis logs and
   the pod events to `RESULTS_DIR/logs`, then deletes everything labelled with the release.

`bench.sh all` does deploy, run and teardown, and runs teardown even when deploy or run fails or
is interrupted.

## What it covers

The workloads use only what the Rust gateway supports:

- the master key and virtual keys with no rate limits, guardrails, tags, team or budget (features
  basic)
- `/v1/chat/completions` to OpenAI, streamed and not
- `/v1/messages` to Anthropic and to Bedrock invoke
- several deployments per model group with simple-shuffle
- contexts from 10k up to 1M tokens, depending on the workload

## Prerequisites

- A Kubernetes cluster on linux/amd64 nodes. The Bifrost and Portkey digests are amd64 images.
- metrics-server, so `metrics.k8s.io` serves pod CPU and memory. Without it the runner falls back
  to a sampler container in each stack pod, and Postgres has no numbers.
- Nodes of one instance type for every gateway, so the comparison is fair. The published runs
  used `m7i.4xlarge` (16 vCPU, 64 GiB), with 4 dedicated cores and 16 GiB per gateway and 8 cores
  for the load generator. Use `NODE_SELECTOR` (and `TOLERATION` for tainted nodes) to keep every pod
  on one pool of such nodes.
- Nodes labelled `topology.kubernetes.io/zone`. The mock, s3stub, Redis and gateways must run in
  Postgres's zone, so no request crosses zones. Cloud clusters set this label; on others, label
  the nodes yourself.
- Room for the stack. With `all-portkey` it holds about 34 cores and 96 GiB of requests: four
  gateways at 4 cores and 16 GiB, the mock and s3stub at 4 cores and 8 GiB each, Postgres at 2 cores
  and 4 GiB, and the runner at 8 cores and 12 GiB.
- On the machine that runs `bench.sh`: bash, `kubectl` pointing at the cluster, `tar`, `git`, and
  Docker with buildx for `images`. A clone of `https://github.com/BerriAI/litellm` for the LiteLLM
  images (`LITELLM_SRC`).

### Permissions

The operator (whoever runs `bench.sh`) needs, in `NAMESPACE`:

- create, get, list, patch and delete on `deployments`, `jobs`, `services`, `configmaps`,
  `secrets`, `serviceaccounts`, `roles` and `rolebindings`
- get, list and delete on `pods`; get on `pods/log`; create on `pods/exec` (to copy results out)
- get and list on `pods` in `metrics.k8s.io`, because Kubernetes only lets you grant a Role what
  you hold yourself
- get on `namespaces`, plus create if `NAMESPACE` does not exist yet
- optionally get on `nodes` (cluster scoped), so deploy can print instance types

The runner gets its own ServiceAccount, Role and RoleBinding per release (`rbac.yaml`): get and
list on `pods` and on `pods` in `metrics.k8s.io`, and get on `pods/log`. It reads no Secrets.
`verify` connects to Postgres with the stack's fixed password.

## Inputs

Every input is an environment variable. `lib.sh` holds the defaults.

| variable | default | meaning |
|---|---|---|
| `RELEASE` | `bench-<UTC time>` with `all`, otherwise required | Prefix and `app.kubernetes.io/instance` label of every object. 1 to 30 lowercase letters, digits and dashes |
| `NAMESPACE` | `ai-gateway-bench` | Where the stack runs. Created if missing |
| `SIDES` | `both` | `both`: Python and Rust. `rust`: Rust alone. `all`: Python, Rust and Bifrost. `rust-bifrost`: Rust and Bifrost. `rust-portkey`: Rust and Portkey. `rust-bifrost-portkey`: Rust, Bifrost and Portkey. `all-portkey`: all four. `direct`: loadgen straight at the mock, no gateway (the overhead floor; features basic). `python`: Python alone |
| `WORKLOADS` | `smoke` | Comma-separated names from `kit/workloads` |
| `ROUNDS` | `2` | Rounds per side, 1 to 9: Python, Rust, Python, Rust, ... |
| `FEATURES` | `basic` | `full` turns on every per-request feature and verifies the records afterwards; see [Full-feature runs](#full-feature-runs). Forces Python spend logs on |
| `PYTHON_SPEND_LOGS` | `on` | `off` sets `general_settings.disable_spend_logs` |
| `SHARED_STATE` | `off` | `on` runs two replicas per gateway on the stack's Redis (`REDIS_*` for coordination; Python also gets `router_settings.redis_*` for router cooldowns). CPU and memory are then summed over both |
| `PORTKEY_WORKERS` | `4` | Node.js processes in the Portkey pod. `4` forks one per core with Node's cluster module; `1` runs the image's single process |
| `LTO` | `fat` | Rust gateway build. `fat` is what ships. `off` builds faster and is for checking the lane only |
| `LITELLM_SHA` | `5990e10ff8adda1a69bec9c7412083daf8ed4b9b` | LiteLLM commit for the Rust gateway, the Python proxy and the migrations |
| `LITELLM_SRC` | (required for `images`) | Path to a clone of BerriAI/litellm. The commit is checked out in a temporary worktree |
| `REGISTRY` | (required for `images`, `deploy` and `run`) | Registry and prefix the cluster can pull from, such as `ghcr.io/you/bench`. Log in with `docker login` first |
| `PUSH` | `1` | `0` builds into the local Docker daemon instead of pushing |
| `PLATFORM` | `linux/amd64` | Build platform |
| `RUST_IMAGE`, `PYTHON_IMAGE`, `MIGRATIONS_IMAGE`, `TOOLS_IMAGE` | derived from `REGISTRY` | Set any of them to use an image you built elsewhere |
| `BIFROST_IMAGE`, `PORTKEY_IMAGE` | from `bifrost.version`, `portkey.version` | The published images, by digest |
| `NODE_SELECTOR` | (none) | `key=value[,key=value]`, applied to every pod |
| `TOLERATION` | (none) | `key=value:Effect` or `key:Effect`, comma-separated, applied to every pod |
| `GATEWAY_CPU` | `4` | Cores per gateway pod, requests equal to limits. Also Bifrost's `GOMAXPROCS` |
| `GATEWAY_MEMORY_GI` | `16` | GiB per gateway pod. Bifrost's `GOMEMLIMIT` and Portkey's heap get this less 2 GiB |
| `PYTHON_WORKERS` | `GATEWAY_CPU` | The Python proxy's worker processes |
| `CLIENT_CPU` | `8` | Cores requested by the load generator |
| `CLIENT_MEMORY_REQUEST`, `CLIENT_MEMORY_LIMIT` | `12Gi`, `16Gi` | The load generator's memory |
| `RUN_TIMEOUT_MIN` | `420` | The runner stops `run.sh` after this many minutes |
| `RESULTS_DIR` | `lanes/k8s/results/<release>` | Where results and logs land on your machine |
| `KIT_DIR` | `../../kit` | The load-test kit |

## Reproduce the published numbers

Set up once:

```sh
cd lanes/k8s
export REGISTRY=<registry>/<prefix>          # docker login to it first
export LITELLM_SRC=~/src/litellm             # a clone of github.com/BerriAI/litellm
export NODE_SELECTOR=node.kubernetes.io/instance-type=m7i.4xlarge
SIDES=all-portkey ./bench.sh images          # Rust, Python, migrations and tools images
```

`images` builds only what `SIDES` needs, so building once with `all-portkey` covers every run below.

(a) The full-feature sweep. Python, Rust and Bifrost run every feature and `verify` checks their
records. Portkey runs the same requests without features, since its open-source gateway has none.

```sh
SIDES=all-portkey FEATURES=full ROUNDS=1 \
  WORKLOADS=sweep-10,sweep-25,sweep-50,sweep-75,sweep-100 ./bench.sh all
```

(b) The basic run at 50 rps:

```sh
SIDES=all-portkey FEATURES=basic ROUNDS=1 WORKLOADS=sweep-50 ./bench.sh all
```

(c) The direct-to-mock floor, to subtract from every side's overhead:

```sh
SIDES=direct ROUNDS=1 WORKLOADS=sweep-10,sweep-25,sweep-50,sweep-75,sweep-100 ./bench.sh all
```

(d) 1M-token streams:

```sh
SIDES=rust-bifrost-portkey FEATURES=basic ROUNDS=1 WORKLOADS=stream-1m ./bench.sh all
```

The published numbers are one round of each. The default is 2 rounds.

To keep the stack between runs, use the steps one by one with the same `RELEASE`:

```sh
export RELEASE=bench-1 SIDES=all-portkey FEATURES=basic
./bench.sh deploy
WORKLOADS=sweep-50 ./bench.sh run
WORKLOADS=sweep-75 RESULTS_DIR=results/bench-1-sweep-75 ./bench.sh run
./bench.sh teardown
```

`SIDES`, `FEATURES`, `SHARED_STATE`, `PYTHON_SPEND_LOGS` and `PORTKEY_WORKERS` shape the deployed
stack, so keep them the same for `run` as for `deploy`. If your connection drops during a run, the
runner keeps going; `./bench.sh collect` with the same `RELEASE` follows it again and copies the
results. The runner pod waits up to 2 hours after `run.sh` ends for that.

## Results

`RESULTS_DIR` (default `lanes/k8s/results/<release>/`) holds:

| file | what |
|---|---|
| `comparison.md` | The comparison table, then the load on the client, mock, s3stub and Postgres per run |
| `verify/report.md` | With features full: every check per side, and what only one side records |
| `<workload>/<side>-r<round>.jsonl` | One line per request from loadgen |
| `<workload>/<side>-r<round>.log` | loadgen's log, with the shed count |
| `<workload>/summary-<side>.md` | `summarize` tables: overhead by stage, class and context size |
| `probes-<side>.jsonl` | The preflight requests, which verify counts as sent |
| `resources.jsonl` | Every CPU and memory sample |
| `windows.jsonl` | Start and end of every run |
| `inputs.env` | The inputs and image references of the run |
| `run.log` | The runner's whole log |
| `logs/` | After teardown: gateway, mock, s3stub, Postgres and Redis logs, pod restarts, events |

`run.sh` exits non-zero when the preflight fails, loadgen fails, a `summarize` fails or a verify
check fails, and
`bench.sh run` passes that on.

## Full-feature runs

With `FEATURES=full`, `genconfig -features full -key-sets python,rust` gives each side its own
1,000 keys, 100 users and 10 teams. Every key, user, team and team member has rpm, tpm and a budget,
every key has `max_parallel_requests`, and every deployment has rpm and tpm. The limits are far above
any workload, so every check runs and nothing is refused. Both configs list
`dynamic_rate_limiter_v3`, `prometheus` and `s3_v2` (into `<release>-s3stub`, one prefix per side).
The Rust gateway runs its four request hooks (`forward_headers`, `spend_metadata`,
`strip_headers`, `provider_metadata`), and Python runs stand-ins for them from
`custom_callbacks.py`. loadgen sends the headers those hooks act on.

After the comparison, `verify` checks each side's records against everything it was sent,
preflight included, and writes `verify/report.md`:

- spend log rows, tokens and per-group totals, plus rows priced at $0, rows naming the team, user
  and deployment, and spend metadata
- key, team, user and member spend, and the daily user and team tables, each against the spend logs
- request, token and spend counters on `/metrics`, scraped from every replica
- S3 request log count, tokens and cost
- the headers the mock received: forwarded, stripped and added

Across sides it compares each deployment's input and output price, and it lists the metrics, labels
and S3 fields only one side records. Any failed check fails the run. Spend logs are written in
batches, so verify first waits (up to 15 minutes) until every side's rows are in.

If the namespace has a Secret named `litellm-license` (key `LITELLM_LICENSE`), the Python proxy
reads it. Nothing else does.

## Bifrost

Sides `all`, `rust-bifrost`, `rust-bifrost-portkey` and `all-portkey` run open-source Bifrost after
Python and Rust in each round, with the same seed. The version is pinned in `bifrost.version`. The
lane runs Bifrost's own published image (`maximhq/bifrost`), unchanged and pinned by digest. Its
entrypoint reads `APP_DIR`, `APP_PORT`, `APP_HOST` and `LOG_LEVEL` from the environment. Its pod gets
the same 4 cores and 16 GiB as the other gateways, with `GOMAXPROCS=4` and `GOMEMLIMIT=14GiB`. It
loads genconfig's `bifrost` key set (`bifrost-config.json`). It keeps its config and logs in a
`bifrost` database in the stack's Postgres, which its init container creates.

- **Requests:** the same deployments, groups and keys as the other sides. Chat goes to
  `/v1/chat/completions` and messages to `/anthropic/v1/messages`. Bedrock deployments go to the
  mock's TLS listener through Converse, because Bifrost's Bedrock provider only speaks HTTPS. That
  TLS is a small cost the other sides don't pay.
- **Cross-replica state:** Bifrost has none. Rate limits, budgets and counters live in each
  replica's memory and are written to Postgres every 10 s, with no Redis. So it runs one replica
  even with shared state on, and the comparison says so.
- **Full features**, mapped onto open-source Bifrost:
  - key, team and `customer` budgets and rate limits (Bifrost's `customer` entity stands in for
    the user, since users and team members are enterprise-only)
  - model configs for deployment limits
  - the telemetry plugin's `/metrics`
  - the logs store with S3 payloads under `bifrost/`
  - header forwarding and stripping
  - `logging_headers` for spend metadata
- **Verify:** reads Bifrost's records in its own schema. Users, team members, daily tables, key
  metadata and provider_metadata (`x-caller-team`) are reported as n/a.

## Portkey

Sides `rust-portkey`, `rust-bifrost-portkey` and `all-portkey` run the open-source Portkey gateway
after the others in each round, with the same seed. The version is pinned in `portkey.version`. The
lane runs Portkey's own published image (`portkeyai/gateway`), unchanged and pinned by digest. Its
pod gets the same 4 cores and 16 GiB as the other gateways.

- **Processes:** the image runs one Node.js process, which uses about one core. With
  `PORTKEY_WORKERS=4` (the default) the pod's command forks four copies of the same entry point with
  Node's cluster module, sharing the port, each with a 3.5 GiB heap. `1` runs one process with a
  14 GiB heap.
- **Requests:** Portkey keeps no model list, keys or database. Each request carries its routing in
  an `x-portkey-config` header, which genconfig's `portkey` key set writes per model group: it
  load-balances evenly over the group's same four deployments on the mock, with two retries.
  loadgen and the preflight send it (`-model-headers`). Chat goes to `/v1/chat/completions` and
  messages to `/v1/messages`. Bedrock deployments go to the mock's Converse API over plain HTTP,
  SigV4 signed. The pod sets `TRUSTED_CUSTOM_HOSTS` to the mock's name, since Portkey refuses a
  `.local` custom host otherwise.
- **Features:** none. The open-source gateway has no client auth, virtual keys, limits, budgets,
  spend logs or metrics; those are in Portkey's hosted and enterprise products. So it does less per
  request than Rust with basic features, which still checks every virtual key. It does validate and
  parse the routing header on every request, and its log middleware copies each non-streamed
  response even with no log viewer attached; both are part of the published gateway. With
  `FEATURES=full` the other sides run their features and Portkey runs the same routing without
  them. The comparison says so, and verify skips Portkey.
- **State:** none, so one replica whatever `SHARED_STATE` is.

## Sustained rates

`sustained-100`, `sustained-500` and `sustained-1000` hold one arrival rate for 15 minutes (after a
1-minute warmup) with evenly spaced arrivals and smoke's request mix over all 20 groups: chat and
messages 50/50, half streaming, 10k to 50k token prompts, 1 to 5 s per request. `sweep-10` to
`sweep-100` are the same mix for 5 minutes each. List several in one run and every side runs each
rate in turn. `sustained-check` runs the same mix at 100, 500 and 1,000 rps for 2 minutes each, to
see that the client, mock, s3stub and Postgres hold 1,000 rps before paying for the long runs.

Run the high rates with sides `rust-bifrost`. One 4-core Python pod holds about 90 req/s with
features full, so Python is left out rather than measured collapsing.

The load generator has 8 cores. It measured about 1.1 cores and 2.5 GiB at 1,000 rps. The second
table in `comparison.md` gives the client's, mock's, s3stub's and Postgres's CPU and memory per run.

## Reading the numbers

- **First-byte and total overhead** are milliseconds the gateway adds on top of what the mock was
  told to take. These are the headline latency numbers. Subtract the `direct` run's numbers to get
  what the gateway alone adds.
- **Tokens/s** counts successful requests over the run's span. At the same arrival rate every side
  should match, so a gap means failed requests.
- **CPU cores** is the mean over the run. **Peak memory** is the maximum over the run. Both come
  from metrics-server, so they are coarse (about 15 s windows).
- **Valid = no (shed N)** means loadgen dropped arrivals at `max_in_flight`. The generator, not the
  gateway, was the limit, so that run says nothing about the gateway.
- **Client lag p99** is how late loadgen sent requests against their planned arrival. Above 10 ms
  the run is marked invalid for the same reason.
- One gateway pod per side gives comparisons, not absolute capacity. A comparison is only fair when
  the gateways run on the same instance type. Deploy prints each pod's node; check that they match.

## Time

Each run of a side takes the workload's length plus the 30 s settle. `sweep-*` is 6 minutes (1
minute of warmup, then 5), so about 6.5 minutes per side per round. `stream-1m` is 15.5 minutes,
about 16 per side per round. `smoke` is about 1 minute. Add deploy (3 to 12 minutes, more when the
cluster has to add nodes), the preflight, verify with features full (up to 15 minutes of waiting
for spend logs) and teardown (about 1 minute).

So (a) is about 2 hours 10 minutes of runs (5 workloads, 4 sides, 1 round), (b) about 52 minutes
(4 sides, 2 rounds), (c) about 65 minutes and (d) about 1 hour 36 minutes. The first `images` for a
commit takes longest: the fat LTO Rust build and the Python image each take 10 to 30 minutes.

## Dry run without a cluster

`run.sh` takes `PYTHON_URL`, `RUST_URL`, `BIFROST_URL`, `PORTKEY_URL` and `DIRECT_URL` to target
local processes, `WORKLOAD_DIR` for the workloads and `RESULTS_DIR` for its output. Build the kit
(`cd kit && go build -o bin/ ./cmd/...`), put `bin/` and a `kubectl` stub that exits 1 on `PATH`
(the CPU and memory columns stay empty), start `mockllm`, and the whole script runs, from preflight to comparison table, against local processes:

```sh
kit/bin/mockllm -addr 127.0.0.1:9000 &
PATH="$PWD/kit/bin:/path/to/stub:$PATH" RELEASE=dry SIDES=direct WORKLOADS=smoke ROUNDS=1 \
  WORKLOAD_DIR=$PWD/kit/workloads RESULTS_DIR=/tmp/dry DIRECT_URL=http://127.0.0.1:9000 \
  sh lanes/k8s/run.sh
```

These numbers say nothing about performance: the client must run in the cluster, next to the
gateways, for valid numbers.

## Cleaning up

Teardown deletes only objects labelled `app.kubernetes.io/instance=<release>` (and the runner pod,
labelled `<release>-runner`), never by name or namespace. A run interrupted before its teardown
leaves its objects behind; run `RELEASE=<release> ./bench.sh teardown` to remove them. To list what
is left:

```sh
kubectl get all,configmap,secret -n ai-gateway-bench -l app.kubernetes.io/name=ai-gateway-bench
```
