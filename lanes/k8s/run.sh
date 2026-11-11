#!/bin/sh
# Replays each workload against every side in turn, from inside the cluster, and writes the
# comparison and everything behind it to RESULTS_DIR. Runs in the tools image (the load-test kit's
# binaries on alpine/k8s) as the <release>-runner Job that bench.sh run starts.
#
# Per workload, per round r = 1..ROUNDS: each side in order (python, rust, bifrost, portkey, as SIDES
# selects), each with loadgen -seed r, so every side gets the identical request sequence (arrival
# times, classes, context sizes, keys). Pod CPU and memory come from metrics.k8s.io every 10 s for
# the whole run. Bifrost gets the same requests with its own key set, its messages endpoint at
# /anthropic/v1/messages. Portkey takes its routing from an x-portkey-config header on each request:
# loadgen and the preflight send the one genconfig wrote for the request's model group
# (out/portkey-headers.json), and the key from keys-portkey.txt, which Portkey never checks.
#
# FEATURES=full gives each side its own key set and the request headers its hooks act on, and after
# the runs checks every record each side wrote against what it was sent (verify, in the load-test
# kit): spend logs, key/user/team/member spend, daily tables, /metrics on every replica, the S3
# request logs and the headers the mock received.
#
# Env: RELEASE, NAMESPACE, WORKLOADS (comma-separated), ROUNDS, SIDES, FEATURES (basic|full),
# PORTKEY_WORKERS and GATEWAY_CPU (for the report text), RESULTS_DIR (default: a temporary directory),
# WORKLOAD_DIR (default /workloads, where the tools image keeps them).
# PYTHON_URL / RUST_URL / BIFROST_URL / PORTKEY_URL / DIRECT_URL override the in-cluster addresses,
# for a dry run against local processes (with kubectl stubbed).
set -eu

RELEASE="${RELEASE:?}"
WORKLOADS="${WORKLOADS:-smoke}"
ROUNDS="${ROUNDS:-2}"
FEATURES="${FEATURES:-basic}"
WORKLOAD_DIR="${WORKLOAD_DIR:-/workloads}"
GATEWAY_CPU="${GATEWAY_CPU:-4}"
CLIENT_CPU="${CLIENT_CPU:-8}"
case "$FEATURES" in
  basic | full) ;;
  *)
    echo "FEATURES must be basic or full, got: ${FEATURES}" >&2
    exit 1
    ;;
esac
NS="${NAMESPACE:-ai-gateway-bench}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# SIDES names a set of sides; from here on SIDES is the list itself.
SIDES_MODE="${SIDES:-both}"
case "$SIDES_MODE" in
  both) SIDES="python rust" ;;
  rust) SIDES="rust" ;;
  all) SIDES="python rust bifrost" ;;
  rust-bifrost) SIDES="rust bifrost" ;;
  rust-portkey) SIDES="rust portkey" ;;
  rust-bifrost-portkey) SIDES="rust bifrost portkey" ;;
  all-portkey) SIDES="python rust bifrost portkey" ;;
  direct) SIDES="direct" ;;
  # The Python gateway alone, for a LiteLLM commit whose Rust gateway cannot serve the lane's keys.
  python) SIDES="python" ;;
  *)
    echo "SIDES must be both, rust, all, rust-bifrost, rust-portkey, rust-bifrost-portkey, all-portkey, direct or python, got: ${SIDES_MODE}" >&2
    exit 1
    ;;
esac
# Idle time between runs, so one side's closing connections and GC do not land in the next run.
SETTLE_S=30
# A run whose requests left this late (p99, ms) against their planned arrival says more about the
# load generator than the gateway, and is marked invalid.
MAX_CLIENT_LAG_MS=10

case "$ROUNDS" in
  [1-9]) ;;
  *)
    echo "rounds must be 1 to 9, got: $ROUNDS" >&2
    exit 1
    ;;
esac
for w in $(echo "$WORKLOADS" | tr ',' ' '); do
  case "$w" in
    *[!a-z0-9-]*)
      echo "workload names are lowercase letters, digits and dashes, got: $w" >&2
      exit 1
      ;;
  esac
  [ -f "${WORKLOAD_DIR}/${w}.json" ] || { echo "no workload ${WORKLOAD_DIR}/${w}.json in the tools image" >&2; exit 1; }
done
WORKLOAD_DIR="$(cd "$WORKLOAD_DIR" && pwd)"

url() {
  case "$1" in
    python) echo "${PYTHON_URL:-http://${RELEASE}-python.${NS}.svc.cluster.local:4000}" ;;
    rust) echo "${RUST_URL:-http://${RELEASE}-rust.${NS}.svc.cluster.local:4000}" ;;
    bifrost) echo "${BIFROST_URL:-http://${RELEASE}-bifrost.${NS}.svc.cluster.local:4000}" ;;
    portkey) echo "${PORTKEY_URL:-http://${RELEASE}-portkey.${NS}.svc.cluster.local:4000}" ;;
    # The mock serves /v1/chat/completions and /v1/messages without a deployment prefix too.
    direct) echo "${DIRECT_URL:-http://${RELEASE}-mockllm.${NS}.svc.cluster.local:9000}" ;;
  esac
}
# Where a side serves Anthropic messages.
messages_path() {
  if [ "$1" = bifrost ]; then echo /anthropic/v1/messages; else echo /v1/messages; fi
}

WORK="$(mktemp -d)"
# Everything below writes to results/, which is RESULTS_DIR (bench.sh copies it out of the pod).
RESULTS_DIR="${RESULTS_DIR:-${WORK}/out-results}"
mkdir -p "$RESULTS_DIR"
RESULTS_DIR="$(cd "$RESULTS_DIR" && pwd)"
cd "$WORK"
ln -s "$RESULTS_DIR" results
echo "results: ${RESULTS_DIR}"
# loadgen reads keys.file (out/keys.txt) relative to the working directory; genconfig.sh's fixed key
# seed makes these the keys the seed Job loaded. Rendered from the lane's own copy, not read back
# from the stack's ConfigMap: the runner's service account may read pod metrics and nothing else.
sed -e "s|__RELEASE__|${RELEASE}|g" -e "s|__NAMESPACE__|${NS}|g" -e "s|__FEATURES__|${FEATURES}|g" \
  -e "s|__SIDES__|${SIDES_MODE}|g" "$HERE/genconfig.sh" > genconfig.sh
sh genconfig.sh out
: > results/resources.jsonl

# A side's keys, and with features full the request headers its hooks act on (one per line).
# Bifrost always has its own (sk-bf- keys, from genconfig's bifrost set), and so does Portkey.
keys_file() {
  case "$FEATURES/$1" in
    full/* | */bifrost | */portkey) echo "out/keys-$1.txt" ;;
    *) echo out/keys.txt ;;
  esac
}
# loadgen flags for headers that depend on the model group: Portkey's routing config.
model_headers_args() {
  if [ "$1" = portkey ]; then echo "-model-headers out/portkey-headers.json"; fi
}
# A model group's routing header for the preflight's curl, or nothing.
model_header() {
  if [ "$1" = portkey ]; then
    printf 'x-portkey-config: %s' "$(jq -r --arg m "$2" '.[$m]["x-portkey-config"]' out/portkey-headers.json)"
  fi
}
headers_file() {
  # Portkey has no hooks for these headers to drive (features full has no Portkey form).
  if [ "$FEATURES" = full ] && [ "$1" != portkey ]; then echo "out/headers-$1.txt"; else echo /dev/null; fi
}

echo "--- preflight: every model group answers on each gateway (${SIDES})"
# Fails before any load if a side rejects the keys, a route or a provider. Every group is hit
# several times so simple-shuffle reaches the Bedrock and Anthropic deployments of each.
# Each probe that succeeds is recorded like a loadgen result (in= fixes its input tokens), so verify
# counts it among what the side was sent.
probe() {
  side="$1" path="$2" model="$3" body="$4"
  : > /tmp/probe.out
  # An empty -H is ignored, so sides without a routing header send the same request as before.
  code="$(curl -sS -o /tmp/probe.out -w '%{http_code}' --max-time 60 \
    -H "Authorization: Bearer $(head -n1 "$(keys_file "$side")")" -H 'content-type: application/json' \
    -H "$(model_header "$side" "$model")" \
    -H "@$(headers_file "$side")" -d "$body" "$(url "$side")${path}")" || code=000
  if [ "$code" != 200 ]; then
    echo "${side} ${path} -> ${code}: $(head -c 500 /tmp/probe.out)" >&2
    return 1
  fi
  printf '{"model":"%s","ctx":100,"out":5,"ok":true}\n' "$model" >> "results/probes-${side}.jsonl"
}
failed=0
for side in $SIDES; do
  : > "results/probes-${side}.jsonl"
  for g in 0 1 2 3 4 5 6 7 8 9; do
    for _ in 1 2 3 4; do
      probe "$side" /v1/chat/completions "gpt-6-astra-${g}" \
        "{\"model\":\"gpt-6-astra-${g}\",\"messages\":[{\"role\":\"user\",\"content\":\"[[mock ttft_ms=0 dur_ms=10 out=5 in=100]] ping\"}]}" || failed=1
      probe "$side" "$(messages_path "$side")" "claude-opus-5-5-${g}" \
        "{\"model\":\"claude-opus-5-5-${g}\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"[[mock ttft_ms=0 dur_ms=10 out=5 in=100]] ping\"}]}" || failed=1
    done
  done
  echo "${side}: probes done"
done
if [ "$failed" != 0 ]; then
  echo "preflight failed; not starting load" >&2
  exit 1
fi

echo "--- sampling CPU and memory: gateways, mock, s3stub, Postgres and this load generator"
# The sampler drops kubectl's errors, so say once up front when metrics.k8s.io can't be read and the
# CPU and memory columns will be empty.
METRICS_API=yes
if ! kubectl get --raw "/apis/metrics.k8s.io/v1beta1/namespaces/${NS}/pods" > /dev/null 2> metrics.err; then
  METRICS_API=no
  echo "metrics.k8s.io is not readable in ${NS} ($(head -c 300 metrics.err)); reading each pod's sampler container instead (Postgres has none)" >&2
fi
# Every 10 s: each gateway pod (side), the mock and s3stub (role), Postgres (role postgresql) from
# metrics.k8s.io, and this pod's own cgroup (role client), so each run shows that nothing but the
# gateway was near its limit.
(
  prev_u="" prev_t=""
  while :; do
    t="$(date +%s)"
    # Without metrics-server, the last line of each stack pod's sampler (stack.yaml) instead.
    if [ "$METRICS_API" = no ]; then
      kubectl get pods -n "$NS" -l "app.kubernetes.io/instance=${RELEASE}" -o json 2>/dev/null |
        jq -r '.items[] | select(any(.spec.containers[]; .name == "sampler"))
          | [.metadata.name, (.metadata.labels["bench/side"] // "-"), (.metadata.labels["bench/role"] // "-")] | join("|")' |
        while IFS='|' read -r pod side role; do
          # The raw log URL, so only the pods/log permission is needed.
          kubectl get --raw "/api/v1/namespaces/${NS}/pods/${pod}/log?container=sampler&tailLines=1" 2>/dev/null |
            jq -c --argjson t "$t" --arg side "$side" --arg role "$role" 'select(.cpu)
              | {t: $t, side: (if $side == "-" then null else $side end),
                 role: (if $side == "-" then $role else $side end), cpu, mem}' >> results/resources.jsonl || true
        done
    fi
    # Postgres and Redis carry the release label too (role postgresql and redis); this runner's
    # pod does not (it is <release>-runner), so it is sampled from its own cgroup below.
    if [ "$METRICS_API" = yes ]; then
      kubectl get --raw "/apis/metrics.k8s.io/v1beta1/namespaces/${NS}/pods?labelSelector=app.kubernetes.io%2Finstance%3D${RELEASE}" 2>/dev/null |
        jq -c --argjson t "$t" '.items[]
          | (.metadata.labels["bench/side"]) as $side
          | ([.containers[] | select(.name == "gateway")][0] // [.containers[] | select(.name != "sampler")][0]) as $c
          | {t: $t, side: $side,
             role: ($side // .metadata.labels["bench/role"] // .metadata.labels["app.kubernetes.io/name"]),
             cpu: $c.usage.cpu, mem: $c.usage.memory}' >> results/resources.jsonl || true
    fi
    # Cumulative CPU microseconds (cgroup v2, or v1's nanoseconds), so cores are the difference over
    # the interval.
    u="$(sed -n 's/^usage_usec //p' /sys/fs/cgroup/cpu.stat 2>/dev/null || true)"
    m="$(cat /sys/fs/cgroup/memory.current 2>/dev/null || true)"
    if [ -z "$u" ] && [ -r /sys/fs/cgroup/cpuacct/cpuacct.usage ]; then
      u="$(($(cat /sys/fs/cgroup/cpuacct/cpuacct.usage) / 1000))"
      m="$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || true)"
    fi
    if [ -n "$u" ] && [ -n "$prev_u" ] && [ "$t" -gt "$prev_t" ]; then
      printf '{"t":%s,"side":null,"role":"client","cpu":"%sn","mem":"%sKi"}\n' \
        "$t" "$(((u - prev_u) * 1000 / (t - prev_t)))" "$((${m:-0} / 1024))" >> results/resources.jsonl
    fi
    prev_u="$u" prev_t="$t"
    sleep 10
  done
) &
SAMPLER=$!
trap 'kill "$SAMPLER" 2>/dev/null || true' EXIT

rc=0
for w in $(echo "$WORKLOADS" | tr ',' ' '); do
  mkdir -p "results/${w}"
  r=1
  while [ "$r" -le "$ROUNDS" ]; do
    for side in $SIDES; do
      echo "--- ${w} round ${r}: ${side}"
      start="$(date +%s)"
      set --
      while IFS= read -r h; do
        [ -n "$h" ] && set -- "$@" -header "$h"
      done < "$(headers_file "$side")"
      # shellcheck disable=SC2046 # model_headers_args is a flag pair or nothing
      if ! loadgen -workload "${WORKLOAD_DIR}/${w}.json" -target "$(url "$side")" -seed "$r" \
          -keys "$(keys_file "$side")" -messages-path "$(messages_path "$side")" $(model_headers_args "$side") "$@" \
          -out "results/${w}/${side}-r${r}.jsonl" 2> "results/${w}/${side}-r${r}.log"; then
        echo "loadgen exited non-zero for ${w} ${side} round ${r}" >&2
        rc=1
      fi
      end="$(date +%s)"
      tail -n 3 "results/${w}/${side}-r${r}.log"
      printf '{"workload":"%s","side":"%s","round":%s,"start":%s,"end":%s}\n' \
        "$w" "$side" "$r" "$start" "$end" >> results/windows.jsonl
      sleep "$SETTLE_S"
    done
    r=$((r + 1))
  done
  for side in $SIDES; do
    summarize "results/${w}/${side}"-r*.jsonl > "results/${w}/summary-${side}.md" || rc=1
  done
done
kill "$SAMPLER" 2>/dev/null || true

echo "--- comparison"
{
  if [ "$SIDES" = rust ]; then
    echo "### Rust gateway alone (no Python comparison in this run)"
  elif [ "$SIDES" != "python rust" ]; then
    case "$SIDES" in
      "rust bifrost") echo "### Rust gateway vs Bifrost, same mock, same requests (no Python: one 4-core Python pod holds about 90 req/s with features full)" ;;
      "rust portkey") echo "### Rust gateway vs Portkey, same mock, same requests (no Python)" ;;
      "rust bifrost portkey") echo "### Rust gateway vs Bifrost vs Portkey, same mock, same requests (no Python)" ;;
      "python rust bifrost portkey") echo "### Python vs Rust gateway vs Bifrost vs Portkey, same mock, same requests" ;;
      direct) echo "### Direct to the mock, no gateway: the floor to subtract from every side's overhead" ;;
      python) echo "### Python gateway alone (no Rust comparison in this run)" ;;
      *) echo "### Python vs Rust gateway vs Bifrost, same mock, same requests" ;;
    esac
    echo
    case "$SIDES" in
      *portkey*)
        echo "Portkey is the open-source Portkey gateway $(sed -n 's|^PORTKEY_TAG=||p' "$HERE/portkey.version"), its published image $(sed -n 's|^PORTKEY_SOURCE_IMAGE=||p' "$HERE/portkey.version"), run as ${PORTKEY_WORKERS:-4} Node.js process(es) in one pod. It does less per request than the other sides: it has no client auth, virtual keys, limits, budgets, spend logs or metrics (those are in Portkey's hosted and enterprise products), so it checks no key and writes nothing. It takes each request's routing from an x-portkey-config header (one per model group, load-balancing over the same deployments) and validates and parses that header on every request. Its Bedrock calls go to the mock's Converse API over plain HTTP."
        if [ "$FEATURES" = full ]; then
          echo
          echo "**Features full on every side but Portkey.** Python, Rust and Bifrost check each key and run the limits, budgets, spend logs, S3 request logs and metrics, and verify checks their records after the runs. Portkey serves the same requests with none of that: its open-source gateway has no such features, so its numbers are for routing alone and verify skips it."
        fi
        echo
        ;;
    esac
    case "$SIDES" in *bifrost*) echo "Bifrost is open-source Bifrost $(sed -n 's|^BIFROST_TAG=transports/||p' "$HERE/bifrost.version"), its published image $(sed -n 's|^BIFROST_SOURCE_IMAGE=||p' "$HERE/bifrost.version"), with each side's features mapped onto its own (see the verify report). It has no cross-replica state at all (limits, budgets and counters live in each replica), so it runs one replica even with shared state on. Its Bedrock calls go to the mock over TLS, which the other sides do not pay." ;; esac
  else
    echo "### Python vs Rust gateway, same SHA, same mock, same keys"
  fi
  echo
  echo "Workloads ${WORKLOADS}, ${ROUNDS} round(s) per side (${SIDES}). Overheads are milliseconds the gateway adds on top of the mock's planned time. Tokens/s count successful requests. CPU is the gateway containers' mean over the run (cores, of ${GATEWAY_CPU} per replica, summed over replicas); memory is their summed peak. Client lag is how late loadgen sent requests against their planned arrival. A run is marked invalid when loadgen shed requests or its lag p99 passed ${MAX_CLIENT_LAG_MS} ms: the generator, not the gateway, was the limit."
  echo
  echo "| workload | side | round | requests | ok % | input tok/s | output tok/s | first-byte overhead p50 / p99 | total overhead p50 / p90 / p99 | stream gap p99 | client lag p99 | CPU cores | peak memory MiB | valid |"
  echo "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
  while read -r line; do
    w="$(echo "$line" | jq -r .workload)"
    side="$(echo "$line" | jq -r .side)"
    r="$(echo "$line" | jq -r .round)"
    shed="$(sed -n 's/.*done: .* shed=\([0-9]*\).*/\1/p' "results/${w}/${side}-r${r}.log" | tail -n1)"
    valid=yes
    [ "${shed:-0}" = 0 ] || valid="no (shed ${shed})"
    jq -rs --argjson win "$line" --arg valid "$valid" --argjson maxlag "$MAX_CLIENT_LAG_MS" \
      --slurpfile res results/resources.jsonl -f "$HERE/compare.jq" "results/${w}/${side}-r${r}.jsonl"
  done < results/windows.jsonl
  echo
  echo "Load on everything else during each run (mean CPU cores / peak memory MiB): a gateway's numbers only count while these stay well under their limits (client ${CLIENT_CPU} cores, mock 4, s3stub 4, Postgres 2 or 4)."
  echo
  echo "| workload | side | round | client | mock | s3stub | Postgres |"
  echo "|---|---|---|---|---|---|---|"
  while read -r line; do
    jq -rn --argjson win "$line" --slurpfile res results/resources.jsonl -f "$HERE/stack.jq"
  done < results/windows.jsonl
  echo
  echo "Per-class and per-context-size tables: \`<workload>/summary-<side>.md\` in the results directory."
} > results/comparison.md
cat results/comparison.md

if [ "$FEATURES" = full ]; then
  echo "--- verify: what each side recorded against what it was sent"
  # The stack's fixed, deliberately non-secret password (deps.yaml); the runner's service account
  # cannot read Secrets.
  DB="postgresql://litellm:bench-not-a-secret@${RELEASE}-postgresql.${NS}.svc.cluster.local:5432/litellm"
  # Bifrost's records are in its own database (stack.yaml creates it).
  side_db() {
    if [ "$1" = bifrost ]; then echo "${DB%/litellm}/bifrost"; else echo "$DB"; fi
  }
  mkdir -p results/verify
  # Portkey keeps no records to check.
  VERIFY_SIDES="$(echo "$SIDES" | tr ' ' '\n' | { grep -vx portkey || true; } | tr '\n' ' ')"
  checks=""
  for side in $VERIFY_SIDES; do
    verify expect -out "results/verify/expected-${side}.json" results/*/"${side}"-r*.jsonl "results/probes-${side}.jsonl"
    checks="${checks} -side ${side}=${RELEASE}-${side}-pods.${NS}.svc.cluster.local:4000,${side}"
  done
  # Spend logs are written in batches; wait for every side's rows, then one S3 batch interval more.
  deadline=$(($(date +%s) + 900))
  for side in $VERIFY_SIDES; do
    want="$(jq .sent "results/verify/expected-${side}.json")"
    while :; do
      got="$(verify sql -set "$side" | psql "$(side_db "$side")" -At | jq .rows)" || got=0
      [ "${got:-0}" -ge "$want" ] && break
      if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "${side}: ${got} of ${want} spend log rows after 15 minutes; checking anyway" >&2
        break
      fi
      sleep 15
    done
  done
  # Also covers Bifrost writing its in-memory budget usage to Postgres (every 10 s).
  sleep 30
  for side in $VERIFY_SIDES; do
    verify sql -set "$side" | psql "$(side_db "$side")" -At > "results/verify/db-${side}.json"
  done
  # shellcheck disable=SC2086 # checks is a list of -side flags
  if ! verify check -dir results/verify $checks \
      -s3 "http://${RELEASE}-s3stub.${NS}.svc.cluster.local:9100/logs" \
      -mock "http://${RELEASE}-mockllm.${NS}.svc.cluster.local:9000" \
      -report results/verify/report.md > /dev/null; then
    rc=1
  fi
  head -n 1 results/verify/report.md
fi

exit "$rc"
