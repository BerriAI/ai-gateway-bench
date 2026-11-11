#!/usr/bin/env bash
# The Kubernetes lane's one entry point. See README.md for what it measures and the inputs.
#
#   bench.sh images    build and push the LiteLLM images and the tools image to REGISTRY
#   bench.sh deploy    stand up Postgres, the mock, s3stub and the gateways SIDES selects
#   bench.sh run       run the load generator in the cluster and copy its results to RESULTS_DIR
#   bench.sh collect   copy the results of a runner already started (after a lost connection)
#   bench.sh teardown  save the stack's logs to RESULTS_DIR/logs, then delete it by label
#   bench.sh all       deploy, run, and always teardown
#
# Every input is an environment variable (lib.sh has the defaults). deploy, run, collect and
# teardown need RELEASE; all makes one up when it is not set.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

set_results_dir() {
  RESULTS_DIR="${RESULTS_DIR:-${LANE_DIR}/results/${RELEASE}}"
  mkdir -p "$RESULTS_DIR"
  RESULTS_DIR="$(cd "$RESULTS_DIR" && pwd)"
  export RESULTS_DIR
}

cmd_images() {
  "$LANE_DIR/build-images.sh"
}

cmd_deploy() {
  check_release
  check_inputs
  resolve_images
  "$LANE_DIR/deploy.sh"
}

# The runner's pod, once the Job has made one.
runner_pod() {
  kubectl get pods -n "$NAMESPACE" -l "job-name=${RELEASE}-runner" -o name 2> /dev/null | head -n1
}

runner_state() {
  kubectl get -n "$NAMESPACE" "$1" -o jsonpath='{.status.containerStatuses[?(@.name=="runner")].state}' 2> /dev/null
}

cmd_collect() {
  check_release
  set_results_dir
  local pod="" state="" code follower
  for _ in $(seq 1 180); do
    pod="$(runner_pod)"
    [ -n "$pod" ] && break
    sleep 5
  done
  [ -n "$pod" ] || die "no pod for job ${RELEASE}-runner in ${NAMESPACE} after 15 minutes"

  # Wait for the runner to start (the tools image may take a while to pull).
  for _ in $(seq 1 180); do
    state="$(runner_state "$pod")"
    case "$state" in *running* | *terminated*) break ;; esac
    case "$state" in *ImagePull* | *ErrImage* | *CreateContainerConfigError*) echo "runner: ${state}" >&2 ;; esac
    sleep 5
  done
  case "$state" in *running* | *terminated*) ;; *)
    kubectl describe -n "$NAMESPACE" "$pod" >&2 || true
    die "the runner did not start within 15 minutes"
    ;;
  esac

  echo "--- following ${pod} (results go to ${RESULTS_DIR})"
  kubectl logs -f -n "$NAMESPACE" "$pod" -c runner &
  follower=$!
  # shellcheck disable=SC2064 # the follower's pid is fixed now
  trap "kill $follower 2>/dev/null || true" EXIT
  while :; do
    code="$(kubectl get -n "$NAMESPACE" "$pod" -o jsonpath='{.status.containerStatuses[?(@.name=="runner")].state.terminated.exitCode}' 2> /dev/null)" ||
      die "lost ${pod}; it may have been deleted (results are gone with it)"
    [ -n "$code" ] && break
    sleep 15
  done
  kill "$follower" 2> /dev/null || true
  wait "$follower" 2> /dev/null || true

  echo "--- copying results"
  kubectl logs -n "$NAMESPACE" "$pod" -c runner > "${RESULTS_DIR}/run.log" 2>&1 || true
  if ! kubectl exec -n "$NAMESPACE" "$pod" -c holder -- tar -C /results -czf - . | tar -C "$RESULTS_DIR" -xzf -; then
    echo "could not copy /results out of ${pod}" >&2
    code=1
  else
    kubectl exec -n "$NAMESPACE" "$pod" -c holder -- touch /results/.collected || true
  fi
  rm -f "${RESULTS_DIR}/.exit"
  echo "run.sh exited ${code}"
  echo "results: ${RESULTS_DIR}"
  [ -f "${RESULTS_DIR}/comparison.md" ] && echo "  ${RESULTS_DIR}/comparison.md"
  [ -f "${RESULTS_DIR}/verify/report.md" ] && echo "  ${RESULTS_DIR}/verify/report.md"
  return "$code"
}

cmd_run() {
  check_release
  check_inputs
  resolve_images
  set_results_dir
  {
    echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for v in RELEASE NAMESPACE SIDES WORKLOADS ROUNDS FEATURES LTO PYTHON_SPEND_LOGS SHARED_STATE \
      PORTKEY_WORKERS GATEWAY_CPU GATEWAY_MEMORY_GI PYTHON_WORKERS CLIENT_CPU CLIENT_MEMORY_REQUEST \
      CLIENT_MEMORY_LIMIT NODE_SELECTOR TOLERATION LITELLM_SHA RUST_IMAGE PYTHON_IMAGE MIGRATIONS_IMAGE \
      TOOLS_IMAGE BIFROST_IMAGE PORTKEY_IMAGE; do
      echo "${v}=${!v}"
    done
  } > "${RESULTS_DIR}/inputs.env"

  echo "--- runner for ${RELEASE}: ${SIDES}, workloads ${WORKLOADS}, ${ROUNDS} round(s), features ${FEATURES}"
  render "$LANE_DIR/rbac.yaml" | kubectl apply -n "$NAMESPACE" -f -
  apply_configmap "${RELEASE}-lane" "$LANE_DIR/run.sh" "$LANE_DIR/genconfig.sh" "$LANE_DIR/compare.jq" \
    "$LANE_DIR/stack.jq" "$LANE_DIR/bifrost.version" "$LANE_DIR/portkey.version"
  # A runner left from an earlier run of this release goes first, with its pod.
  kubectl delete job -n "$NAMESPACE" "${RELEASE}-runner" --ignore-not-found --cascade=foreground --wait=true
  render "$LANE_DIR/runner.yaml" | kubectl apply -n "$NAMESPACE" -f -
  cmd_collect
}

cmd_teardown() {
  check_release
  set_results_dir
  "$LANE_DIR/teardown.sh"
}

cmd_all() {
  RELEASE="${RELEASE:-bench-$(date -u +%Y%m%d%H%M)}"
  export RELEASE
  check_release
  check_inputs
  set_results_dir
  echo "release ${RELEASE}; results in ${RESULTS_DIR}"
  local rc=0
  trap '"$0" teardown' EXIT
  trap 'exit 130' INT TERM
  "$0" deploy || rc=$?
  if [ "$rc" = 0 ]; then "$0" run || rc=$?; fi
  return "$rc"
}

case "${1:-}" in
  images) cmd_images ;;
  deploy) cmd_deploy ;;
  run) cmd_run ;;
  collect) cmd_collect ;;
  teardown) cmd_teardown ;;
  all) cmd_all ;;
  *) usage ;;
esac
