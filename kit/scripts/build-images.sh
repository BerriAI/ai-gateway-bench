#!/usr/bin/env bash
# Build the LiteLLM Rust gateway image from a LiteLLM checkout and the load-test tools image from
# this kit, tagged with the LiteLLM commit so every result maps back to a build.
#
#   LITELLM_SRC=~/src/litellm scripts/build-images.sh          # fat LTO, what ships (~15 min cold)
#   FAST=1 LITELLM_SRC=~/src/litellm scripts/build-images.sh   # no LTO, for iterating
#   PUSH=1 REGISTRY=<registry>/<prefix> LITELLM_SRC=... scripts/build-images.sh
#
# LITELLM_SHA (default: the commit the published numbers used) is checked out in a worktree of
# LITELLM_SRC, so the checkout itself is left as it is.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
src="${LITELLM_SRC:?set LITELLM_SRC to a clone of https://github.com/BerriAI/litellm}"
sha="${LITELLM_SHA:-5990e10ff8adda1a69bec9c7412083daf8ed4b9b}"
registry="${REGISTRY:-}"
prefix="${registry:+$registry/}"

git -C "$src" fetch -q origin "$sha" 2>/dev/null || true
work="$(mktemp -d)"
trap 'git -C "$src" worktree remove --force "$work" >/dev/null 2>&1 || true' EXIT
git -C "$src" worktree add -q --detach "$work" "$sha"
rust="$work/litellm-rust"

tag="${TAG:-gateway-$(git -C "$work" rev-parse --short=8 HEAD)}"
lto_args=()
if [[ "${FAST:-0}" == 1 ]]; then
  lto_args=(--build-arg LTO=off --build-arg CODEGEN_UNITS=16)
  tag="$tag-nolto"
fi

gateway="${prefix}litellm-rust-gateway:$tag"
tools="${prefix}litellm-loadtest:$tag"

docker build "${lto_args[@]}" -f "$rust/crates/gateway/Dockerfile" -t "$gateway" "$rust"
docker build -t "$tools" "$here"

if [[ "${PUSH:-0}" == 1 ]]; then
  docker push "$gateway"
  docker push "$tools"
fi
echo "gateway image: $gateway"
echo "tools image:   $tools"
