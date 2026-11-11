#!/usr/bin/env bash
# Stands up the stack in NAMESPACE, everything labelled app.kubernetes.io/instance=<release> so
# teardown.sh finds it. Called by bench.sh deploy, which resolves the image references first.
#
#   <release>-credentials  Secret: fixed, non-secret database and Redis passwords
#   <release>-postgresql   Postgres (deps.yaml)
#   <release>-redis        Redis, only with SHARED_STATE=on
#   <release>-migrate      Job: the proxy's migrations at LITELLM_SHA
#   <release>-mockllm      the load-test kit's mock upstream (OpenAI chat, Anthropic messages, Bedrock)
#   <release>-s3stub       in-memory S3 for the gateways' s3_v2 request logs (used with FEATURES=full)
#   <release>-seed         Job: 1,000 virtual keys into LiteLLM_VerificationToken
#   <release>-python       the Python gateway, PYTHON_WORKERS workers on GATEWAY_CPU dedicated cores
#   <release>-rust         the Rust gateway on GATEWAY_CPU dedicated cores
#   <release>-bifrost      open-source Bifrost on GATEWAY_CPU dedicated cores, one replica
#   <release>-portkey      the open-source Portkey gateway on GATEWAY_CPU dedicated cores, one replica
#
# Which gateways start depends on SIDES. Every pod gets NODE_SELECTOR and TOLERATION when set.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

check_release
check_inputs
SIDE_LIST="$(side_list "$SIDES")"

case "$PYTHON_SPEND_LOGS" in
  on) DISABLE_SPEND_LOGS=false ;;
  off) DISABLE_SPEND_LOGS=true ;;
esac

# full: every per-request feature on (genconfig -features full) and the records verified after the
# runs, which needs Python's spend logs; the mock counts the headers the request hooks act on.
MOCK_COUNT_HEADERS=
if [ "$FEATURES" = full ]; then
  MOCK_COUNT_HEADERS=x-request-origin,x-trace-id,x-caller-team,x-cost-center,x-litellm-spend-logs-metadata
  if [ "$DISABLE_SPEND_LOGS" = true ]; then
    echo "FEATURES=full checks Python's spend logs; running with them on"
    DISABLE_SPEND_LOGS=false
  fi
fi

case "$SHARED_STATE" in
  # Two replicas per gateway so there is state to share; both talk to the stack's Redis.
  # Four gateway pods and the mock do not fit on one node, so the gateways go beside it.
  on) SHARED_STATE_ON=true GATEWAY_REPLICAS=2 REDIS_ENV_PREFIX='' MOCK_APART_ROLE=mockllm ;;
  off) SHARED_STATE_ON=false GATEWAY_REPLICAS=1 REDIS_ENV_PREFIX=BENCH_UNUSED_ MOCK_APART_ROLE=none ;;
esac

# Bifrost's Bedrock provider only speaks HTTPS, so with it the mock also serves TLS.
MOCK_TLS_ARGS=
case "$SIDE_LIST" in *bifrost*) MOCK_TLS_ARGS="-tls-addr :9443" ;; esac

for img in TOOLS_IMAGE MIGRATIONS_IMAGE; do
  [ -n "${!img:-}" ] || die "$img is not set (bench.sh deploy sets it from REGISTRY)"
done
case " $SIDE_LIST " in *" python "*) [ -n "${PYTHON_IMAGE:-}" ] || die "PYTHON_IMAGE is not set" ;; esac
case " $SIDE_LIST " in *" rust "*) [ -n "${RUST_IMAGE:-}" ] || die "RUST_IMAGE is not set" ;; esac

export DISABLE_SPEND_LOGS MOCK_COUNT_HEADERS SHARED_STATE_ON GATEWAY_REPLICAS REDIS_ENV_PREFIX \
  MOCK_APART_ROLE MOCK_TLS_ARGS

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Prints the Job's logs and fails when it does not complete.
run_job() {
  local name="$1"
  render "$LANE_DIR/jobs.yaml" | kubectl apply -n "$NAMESPACE" -l "bench/job=${name}" -f -
  if ! kubectl wait -n "$NAMESPACE" --for=condition=complete "job/${RELEASE}-${name}" --timeout=10m; then
    kubectl describe -n "$NAMESPACE" "job/${RELEASE}-${name}" || true
    kubectl logs -n "$NAMESPACE" "job/${RELEASE}-${name}" --all-containers --tail=200 || true
    echo "job ${RELEASE}-${name} did not complete" >&2
    return 1
  fi
  kubectl logs -n "$NAMESPACE" "job/${RELEASE}-${name}" --all-containers --tail=20 || true
}

rollout() {
  if ! kubectl rollout status -n "$NAMESPACE" "deployment/${RELEASE}-$1" --timeout="${2:-10m}"; then
    kubectl describe pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE}" || true
    kubectl logs -n "$NAMESPACE" "deployment/${RELEASE}-$1" --all-containers --tail=200 || true
    exit 1
  fi
}

echo "--- ${RELEASE} in namespace ${NAMESPACE}: sides ${SIDES} (${SIDE_LIST})"
kubectl get namespace "$NAMESPACE" > /dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

echo "--- postgres$([ "$SHARED_STATE_ON" = true ] && echo " and redis")"
render "$LANE_DIR/deps.yaml" | kubectl apply -n "$NAMESPACE" -l bench/part=deps -f -
if [ "$SHARED_STATE_ON" = true ]; then
  render "$LANE_DIR/deps.yaml" | kubectl apply -n "$NAMESPACE" -l bench/part=redis -f -
fi
rollout postgresql 5m
if [ "$SHARED_STATE_ON" = true ]; then rollout redis 5m; fi

echo "--- genconfig script, mockllm and s3stub"
render "$LANE_DIR/genconfig.sh" > "$TMP/genconfig.sh"
apply_configmap "${RELEASE}-genconfig" "$TMP/genconfig.sh"
render "$LANE_DIR/stack.yaml" | kubectl apply -n "$NAMESPACE" -l bench/part=setup -f -

echo "--- migrations"
run_job migrate

echo "--- virtual keys"
run_job seed

echo "--- gateways: ${SIDE_LIST} (features ${FEATURES}, python spend logs $([ "$DISABLE_SPEND_LOGS" = true ] && echo off || echo on), shared state ${SHARED_STATE}, ${GATEWAY_REPLICAS} replica(s) each$(case "$SIDE_LIST" in *bifrost*) echo "; Bifrost 1, it has no cross-replica state" ;; esac)$(case "$SIDE_LIST" in *portkey*) echo "; Portkey 1 with ${PORTKEY_WORKERS} Node.js process(es), it keeps no state" ;; esac))"
GATEWAYS="$(echo "$SIDE_LIST" | tr ' ' '\n' | { grep -vx direct || true; } | tr '\n' ' ')"
if [ -n "${GATEWAYS% }" ]; then
  render "$LANE_DIR/stack.yaml" |
    kubectl apply -n "$NAMESPACE" -l "bench/part=gateways,bench/side in ($(echo "$GATEWAYS" | xargs | tr ' ' ','))" -f -
fi
for d in mockllm s3stub $GATEWAYS; do
  rollout "$d"
done

echo "--- placement"
# A comparison is only fair when every gateway runs on the same instance type. Reading nodes needs
# a cluster-scoped permission the operator may not have, so the second listing is best effort.
kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE}" -o wide
nodes="$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE}" \
  -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort -u | grep . || true)"
if [ -n "$nodes" ]; then
  # shellcheck disable=SC2086 # one argument per node name
  kubectl get nodes $nodes -L node.kubernetes.io/instance-type -L topology.kubernetes.io/zone 2> /dev/null ||
    echo "(cannot read nodes; check the instance types of the nodes above yourself)"
fi
