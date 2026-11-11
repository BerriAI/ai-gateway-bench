#!/bin/sh
# The one place the stack's gateway config and virtual keys are defined. deploy.sh puts this script
# (with __RELEASE__, __NAMESPACE__, __FEATURES__ and __SIDES__ filled in) in the <release>-genconfig
# ConfigMap, where the gateway pods and the seed Job run it; run.sh renders it again in the runner
# to get the same keys. The fixed key seed is what makes every copy produce identical config.yaml,
# keys.txt and keys.sql.
#
# 10 chat and 10 messages groups of 4 deployments (half of each messages group on Bedrock invoke)
# cover every workload in kit/workloads. The master key is synthetic; the stack lives in one
# namespace for one run.
#
# FEATURES=full adds -features full -key-sets python,rust: limits, budgets, callbacks and request
# hooks on, one key set (with its own users and teams) per side so verify can tell their records
# apart, plus custom_callbacks.py and headers-<side>.txt.
#
# SIDES all, rust-bifrost, rust-bifrost-portkey or all-portkey also writes the bifrost key set as a
# Bifrost config (bifrost-config.json, bifrost-pricing.json, keys-bifrost.txt), which only the
# Bifrost pod reads; the other sides' files are the same with or without it. Its Bedrock deployments
# go to the mock's TLS listener, since Bifrost's Bedrock provider only speaks HTTPS.
#
# SIDES rust-portkey, rust-bifrost-portkey or all-portkey also writes the portkey key set:
# portkey-headers.json, the x-portkey-config routing header loadgen sends per model group
# (-model-headers), and keys-portkey.txt. Portkey keeps no config of its own, so only the runner
# reads them. Basic or full features alike (Portkey has none of full's features).
#
#   genconfig.sh <out dir>
# The __WORDS__ are filled in before this runs (deploy.sh, run.sh), so they read as constants here.
# shellcheck disable=SC2194,SC2050
set -eu
features=
bifrost=
# rust-bifrost needs the same files as all (the Python set is written and not used).
case "__SIDES__" in all | rust-bifrost | rust-bifrost-portkey | all-portkey) with_bifrost=yes ;; *) with_bifrost=no ;; esac
case "__SIDES__" in *portkey) with_portkey=yes ;; *) with_portkey=no ;; esac
# basic: the empty set name is the shared keys.txt the Python and Rust gateways use.
sets=
if [ "$with_bifrost" = yes ]; then
  bifrost="-bifrost-bedrock-tls __RELEASE__-mockllm.__NAMESPACE__.svc.cluster.local:9443"
  sets="${sets},bifrost"
fi
[ "$with_portkey" = yes ] && sets="${sets},portkey"
[ -n "$sets" ] && features="-key-sets ${sets}"
if [ "__FEATURES__" = full ]; then
  features="-features full -key-sets python,rust"
  [ "$with_bifrost" = yes ] && features="${features},bifrost"
  [ "$with_portkey" = yes ] && features="${features},portkey"
fi
# shellcheck disable=SC2086 # features and bifrost are flag pairs or nothing
genconfig -out "$1" \
  -mocks http://__RELEASE__-mockllm.__NAMESPACE__.svc.cluster.local:9000 \
  -chat-groups 10 -messages-groups 10 -deployments 4 -bedrock-fraction 0.5 \
  -keys 1000 -key-seed __RELEASE__ -master-key sk-bench-not-a-secret $features $bifrost
