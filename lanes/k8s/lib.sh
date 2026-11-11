# Shared by bench.sh, build-images.sh, deploy.sh and teardown.sh: input defaults and checks,
# image references and the manifest renderer. Sourced, never run. run.sh runs inside the cluster
# in the tools image and does not use it.
# shellcheck shell=bash

LANE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_DIR="${KIT_DIR:-${LANE_DIR}/../../kit}"

NAMESPACE="${NAMESPACE:-ai-gateway-bench}"
SIDES="${SIDES:-both}"
WORKLOADS="${WORKLOADS:-smoke}"
ROUNDS="${ROUNDS:-2}"
FEATURES="${FEATURES:-basic}"
LTO="${LTO:-fat}"
PYTHON_SPEND_LOGS="${PYTHON_SPEND_LOGS:-on}"
SHARED_STATE="${SHARED_STATE:-off}"
PORTKEY_WORKERS="${PORTKEY_WORKERS:-4}"
GATEWAY_CPU="${GATEWAY_CPU:-4}"
GATEWAY_MEMORY_GI="${GATEWAY_MEMORY_GI:-16}"
PYTHON_WORKERS="${PYTHON_WORKERS:-$GATEWAY_CPU}"
CLIENT_CPU="${CLIENT_CPU:-8}"
CLIENT_MEMORY_REQUEST="${CLIENT_MEMORY_REQUEST:-12Gi}"
CLIENT_MEMORY_LIMIT="${CLIENT_MEMORY_LIMIT:-16Gi}"
RUN_TIMEOUT_MIN="${RUN_TIMEOUT_MIN:-420}"
NODE_SELECTOR="${NODE_SELECTOR:-}"
TOLERATION="${TOLERATION:-}"
LITELLM_SHA="${LITELLM_SHA:-5990e10ff8adda1a69bec9c7412083daf8ed4b9b}"
REGISTRY="${REGISTRY:-}"
PLATFORM="${PLATFORM:-linux/amd64}"
export NAMESPACE SIDES WORKLOADS ROUNDS FEATURES LTO PYTHON_SPEND_LOGS SHARED_STATE PORTKEY_WORKERS \
  GATEWAY_CPU GATEWAY_MEMORY_GI PYTHON_WORKERS CLIENT_CPU CLIENT_MEMORY_REQUEST CLIENT_MEMORY_LIMIT \
  RUN_TIMEOUT_MIN NODE_SELECTOR TOLERATION LITELLM_SHA REGISTRY PLATFORM

die() {
  echo "$*" >&2
  exit 1
}

# The gateways (and "direct") one SIDES value runs, in run order.
side_list() {
  case "$1" in
    both) echo "python rust" ;;
    rust) echo "rust" ;;
    all) echo "python rust bifrost" ;;
    rust-bifrost) echo "rust bifrost" ;;
    rust-portkey) echo "rust portkey" ;;
    rust-bifrost-portkey) echo "rust bifrost portkey" ;;
    all-portkey) echo "python rust bifrost portkey" ;;
    # loadgen straight at the mock, no gateway: the floor every side's overhead includes.
    direct) echo "direct" ;;
    # The Python gateway alone, for a LiteLLM commit whose Rust gateway cannot serve the lane's keys.
    python) echo "python" ;;
    *) die "SIDES must be both, rust, all, rust-bifrost, rust-portkey, rust-bifrost-portkey, all-portkey, direct or python, got: $1" ;;
  esac
}

has_side() {
  case " $(side_list "$SIDES") " in *" $1 "*) return 0 ;; *) return 1 ;; esac
}

# Release names prefix every object (<release>-postgresql-<hash>-<hash> is the longest pod name).
check_release() {
  [ -n "${RELEASE:-}" ] || die "set RELEASE (for example RELEASE=bench-1); every object of the stack is named and labelled with it"
  printf '%s' "$RELEASE" | grep -qE '^[a-z]([a-z0-9-]{0,28}[a-z0-9])?$' ||
    die "RELEASE must be 1 to 30 lowercase letters, digits and dashes, starting with a letter, got: $RELEASE"
}

check_int() {
  printf '%s' "$2" | grep -qE '^[1-9][0-9]*$' || die "$1 must be a positive whole number, got: $2"
}

check_inputs() {
  side_list "$SIDES" > /dev/null
  case "$FEATURES" in basic | full) ;; *) die "FEATURES must be basic or full, got: $FEATURES" ;; esac
  case "$LTO" in fat | off) ;; *) die "LTO must be fat or off, got: $LTO" ;; esac
  case "$PYTHON_SPEND_LOGS" in on | off) ;; *) die "PYTHON_SPEND_LOGS must be on or off, got: $PYTHON_SPEND_LOGS" ;; esac
  case "$SHARED_STATE" in on | off) ;; *) die "SHARED_STATE must be on or off, got: $SHARED_STATE" ;; esac
  case "$ROUNDS" in [1-9]) ;; *) die "ROUNDS must be 1 to 9, got: $ROUNDS" ;; esac
  check_int PORTKEY_WORKERS "$PORTKEY_WORKERS"
  check_int GATEWAY_CPU "$GATEWAY_CPU"
  check_int GATEWAY_MEMORY_GI "$GATEWAY_MEMORY_GI"
  check_int PYTHON_WORKERS "$PYTHON_WORKERS"
  check_int RUN_TIMEOUT_MIN "$RUN_TIMEOUT_MIN"
  [ "$GATEWAY_MEMORY_GI" -gt 2 ] || die "GATEWAY_MEMORY_GI must be above 2 (2 GiB is left outside the Go and Node.js heap limits)"
  local w
  for w in $(echo "$WORKLOADS" | tr ',' ' '); do
    case "$w" in *[!a-z0-9-]*) die "workload names are lowercase letters, digits and dashes, got: $w" ;; esac
  done
  printf '%s' "$LITELLM_SHA" | grep -qE '^[0-9a-f]{40}$' || die "LITELLM_SHA must be a full 40-char lowercase commit SHA, got: $LITELLM_SHA"
  if [ "$SIDES" = direct ] && [ "$FEATURES" != basic ]; then
    die "SIDES=direct has no gateway to turn features on in; use FEATURES=basic"
  fi
  node_selector_json > /dev/null
  tolerations_json > /dev/null
}

# NODE_SELECTOR: key=value[,key=value...] -> {"key": "value", ...} (a YAML flow mapping).
node_selector_json() {
  local out="" pair k v
  [ -n "$NODE_SELECTOR" ] || { echo "{}"; return 0; }
  for pair in $(echo "$NODE_SELECTOR" | tr ',' ' '); do
    printf '%s' "$pair" | grep -qE '^[A-Za-z0-9._/-]+=[A-Za-z0-9._-]*$' ||
      die "NODE_SELECTOR must be key=value[,key=value...], got: $NODE_SELECTOR"
    k="${pair%%=*}" v="${pair#*=}"
    out="${out:+$out, }\"$k\": \"$v\""
  done
  echo "{${out}}"
}

# TOLERATION: key=value:Effect or key:Effect (operator Exists), comma-separated for several.
tolerations_json() {
  local out="" t k v e
  [ -n "$TOLERATION" ] || { echo "[]"; return 0; }
  for t in $(echo "$TOLERATION" | tr ',' ' '); do
    if printf '%s' "$t" | grep -qE '^[A-Za-z0-9._/-]+=[A-Za-z0-9._-]*:(NoSchedule|PreferNoSchedule|NoExecute)$'; then
      k="${t%%=*}" v="${t#*=}" e="${v##*:}" v="${v%:*}"
      out="${out:+$out, }{\"key\": \"$k\", \"operator\": \"Equal\", \"value\": \"$v\", \"effect\": \"$e\"}"
    elif printf '%s' "$t" | grep -qE '^[A-Za-z0-9._/-]+:(NoSchedule|PreferNoSchedule|NoExecute)$'; then
      k="${t%%:*}" e="${t##*:}"
      out="${out:+$out, }{\"key\": \"$k\", \"operator\": \"Exists\", \"effect\": \"$e\"}"
    else
      die "TOLERATION must be key=value:Effect or key:Effect (Effect NoSchedule, PreferNoSchedule or NoExecute), comma-separated, got: $TOLERATION"
    fi
  done
  echo "[${out}]"
}

sha256() {
  if command -v sha256sum > /dev/null 2>&1; then sha256sum; else shasum -a 256; fi
}

# The tools image tag: a hash of everything that goes into it (the kit's Go sources and workloads,
# and tools.Dockerfile), so a changed kit gets a new image and an unchanged one is reused.
tools_tag() {
  [ -f "${KIT_DIR}/go.mod" ] || die "no load-test kit at ${KIT_DIR} (set KIT_DIR)"
  {
    (cd "$KIT_DIR" && find . -type f \( -name go.mod -o -name go.sum -o -path './cmd/*' -o -path './workloads/*' \) |
      LC_ALL=C sort | while IFS= read -r f; do printf '%s ' "$f"; sha256 < "$f"; done)
    sha256 < "${LANE_DIR}/tools.Dockerfile"
  } | sha256 | cut -c1-12
}

# Image references. Each one can be set directly; otherwise it is derived from REGISTRY.
# Bifrost and Portkey are always their own published images, pinned by digest.
resolve_images() {
  local prefix="${REGISTRY:+${REGISTRY%/}/}"
  need_registry() {
    [ -n "$REGISTRY" ] || die "set REGISTRY (a registry the cluster can pull from), or set $1 directly"
  }
  if [ -z "${RUST_IMAGE:-}" ]; then
    if has_side rust; then need_registry RUST_IMAGE; fi
    RUST_IMAGE="${prefix}litellm-rust-gateway:${LITELLM_SHA}-lto-${LTO}"
  fi
  if [ -z "${PYTHON_IMAGE:-}" ]; then
    if has_side python; then need_registry PYTHON_IMAGE; fi
    PYTHON_IMAGE="${prefix}litellm-python-gateway:${LITELLM_SHA}"
  fi
  if [ -z "${MIGRATIONS_IMAGE:-}" ]; then
    need_registry MIGRATIONS_IMAGE
    MIGRATIONS_IMAGE="${prefix}litellm-migrations:${LITELLM_SHA}"
  fi
  if [ -z "${TOOLS_IMAGE:-}" ]; then
    need_registry TOOLS_IMAGE
    local tag
    tag="$(tools_tag)" || exit 1
    TOOLS_IMAGE="${prefix}ai-gateway-bench-tools:${tag}"
  fi
  BIFROST_IMAGE="${BIFROST_IMAGE:-$(sed -n 's|^BIFROST_SOURCE_IMAGE=||p' "${LANE_DIR}/bifrost.version")}"
  PORTKEY_IMAGE="${PORTKEY_IMAGE:-$(sed -n 's|^PORTKEY_SOURCE_IMAGE=||p' "${LANE_DIR}/portkey.version")}"
  export RUST_IMAGE PYTHON_IMAGE MIGRATIONS_IMAGE TOOLS_IMAGE BIFROST_IMAGE PORTKEY_IMAGE
}

# Escapes a value for the right-hand side of a sed s||| command.
sed_value() {
  printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'
}

# Fills every __NAME__ placeholder of a manifest or script. Values the caller has not derived yet
# (deploy.sh sets the feature switches) default to the basic-run values.
render() {
  local args=() kv
  for kv in \
    "RELEASE=${RELEASE}" \
    "NAMESPACE=${NAMESPACE}" \
    "PYTHON_IMAGE=${PYTHON_IMAGE:-none}" \
    "MIGRATIONS_IMAGE=${MIGRATIONS_IMAGE:-none}" \
    "RUST_IMAGE=${RUST_IMAGE:-none}" \
    "TOOLS_IMAGE=${TOOLS_IMAGE:-none}" \
    "BIFROST_IMAGE=${BIFROST_IMAGE:-none}" \
    "PORTKEY_IMAGE=${PORTKEY_IMAGE:-none}" \
    "PORTKEY_WORKERS=${PORTKEY_WORKERS}" \
    "PORTKEY_HEAP_MB=$(((GATEWAY_MEMORY_GI - 2) * 1024 / PORTKEY_WORKERS))" \
    "GO_MEMLIMIT=$((GATEWAY_MEMORY_GI - 2))GiB" \
    "GATEWAY_CPU=${GATEWAY_CPU}" \
    "GATEWAY_MEMORY=${GATEWAY_MEMORY_GI}Gi" \
    "PYTHON_WORKERS=${PYTHON_WORKERS}" \
    "CLIENT_CPU=${CLIENT_CPU}" \
    "CLIENT_MEMORY_REQUEST=${CLIENT_MEMORY_REQUEST}" \
    "CLIENT_MEMORY_LIMIT=${CLIENT_MEMORY_LIMIT}" \
    "RUN_TIMEOUT_MIN=${RUN_TIMEOUT_MIN}" \
    "SIDES=${SIDES}" \
    "WORKLOADS=${WORKLOADS}" \
    "ROUNDS=${ROUNDS}" \
    "FEATURES=${FEATURES}" \
    "MOCK_TLS_ARGS=${MOCK_TLS_ARGS:-}" \
    "MOCK_COUNT_HEADERS=${MOCK_COUNT_HEADERS:-}" \
    "DISABLE_SPEND_LOGS=${DISABLE_SPEND_LOGS:-false}" \
    "SHARED_STATE=${SHARED_STATE_ON:-false}" \
    "GATEWAY_REPLICAS=${GATEWAY_REPLICAS:-1}" \
    "REDIS_ENV_PREFIX=${REDIS_ENV_PREFIX:-BENCH_UNUSED_}" \
    "MOCK_APART_ROLE=${MOCK_APART_ROLE:-none}" \
    "NODE_SELECTOR=$(node_selector_json)" \
    "TOLERATIONS=$(tolerations_json)"; do
    args+=(-e "s|__${kv%%=*}__|$(sed_value "${kv#*=}")|g")
  done
  sed "${args[@]}" "$1"
}

# A ConfigMap from local files, labelled with the release so teardown finds it.
apply_configmap() {
  local name="$1"
  shift
  local from=() f
  for f in "$@"; do from+=(--from-file="$f"); done
  kubectl create configmap "$name" -n "$NAMESPACE" "${from[@]}" --dry-run=client -o yaml |
    kubectl label --local -f - -o yaml "app.kubernetes.io/instance=${RELEASE}" app.kubernetes.io/name=ai-gateway-bench |
    kubectl apply -n "$NAMESPACE" -f -
}
