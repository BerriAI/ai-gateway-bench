#!/usr/bin/env bash
# Removes everything deploy.sh and bench.sh run created for RELEASE, after saving the gateway, mock,
# Postgres and Redis logs to RESULTS_DIR/logs so a failed run can still be read. Deletes by label
# only (app.kubernetes.io/instance=<release> or <release>-runner), never by name or namespace.
# Never fails on a missing object. Called by bench.sh teardown.
set -uo pipefail
. "$(dirname "$0")/lib.sh"

check_release
LOGS="${RESULTS_DIR:?}/logs"
mkdir -p "$LOGS"

echo "--- pods before teardown"
kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance in (${RELEASE},${RELEASE}-runner)" -o wide || true

for d in python rust bifrost portkey mockllm s3stub; do
  kubectl logs -n "$NAMESPACE" "deployment/${RELEASE}-${d}" --all-containers=true --tail=-1 --prefix=true \
    > "${LOGS}/${d}.log" 2>&1 || rm -f "${LOGS}/${d}.log"
done
# Postgres and Redis too, with the previous container's log, so a restart or an OOM kill of the
# shared database shows up.
for role in postgresql redis; do
  for p in $(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE},bench/role=${role}" -o name 2>/dev/null); do
    n="${p#pod/}"
    kubectl logs -n "$NAMESPACE" "$p" --all-containers=true --tail=-1 > "${LOGS}/${n}.log" 2>&1 || true
    kubectl logs -n "$NAMESPACE" "$p" --all-containers=true --tail=-1 --previous > "${LOGS}/${n}-previous.log" 2>&1 ||
      rm -f "${LOGS}/${n}-previous.log"
  done
done
kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance in (${RELEASE},${RELEASE}-runner)" \
  -o custom-columns='POD:.metadata.name,NODE:.spec.nodeName,RESTARTS:.status.containerStatuses[*].restartCount,LAST:.status.containerStatuses[*].lastState.terminated.reason,PHASE:.status.phase,REASON:.status.reason' \
  > "${LOGS}/pods.log" 2>&1 || true
kubectl get events -n "$NAMESPACE" --sort-by=.lastTimestamp 2>&1 | grep -E "${RELEASE}" > "${LOGS}/events.log" || true
echo "logs: ${LOGS}"

echo "--- deleting everything labelled app.kubernetes.io/instance=${RELEASE}"
kubectl delete -n "$NAMESPACE" deployment,service,job,configmap,secret,serviceaccount,role,rolebinding \
  -l "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/name=ai-gateway-bench" \
  --ignore-not-found --wait=false || true
kubectl delete -n "$NAMESPACE" pod -l "app.kubernetes.io/instance=${RELEASE}-runner,app.kubernetes.io/name=ai-gateway-bench" \
  --ignore-not-found --wait=false || true
