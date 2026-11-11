#!/usr/bin/env bash
# Builds and pushes the images the lane needs, skipping any already in REGISTRY. Called by
# bench.sh images, which resolves the references (lib.sh resolve_images):
#
#   litellm-rust-gateway:<sha>-lto-<fat|off>  the Rust gateway (litellm-gateway) from
#                                             litellm-rust/crates/gateway/Dockerfile in LITELLM_SRC
#                                             at LITELLM_SHA. fat is what ships. Only with a rust side.
#   litellm-python-gateway:<sha>              the Python proxy from gateway/Dockerfile at the same
#                                             commit (4 workers via NUM_WORKERS; its native extension
#                                             is the shipped build). Only with a python side.
#   litellm-migrations:<sha>                  the proxy's Prisma migrations from migrations/Dockerfile
#                                             at the same commit: the schema every side's keys live in
#   ai-gateway-bench-tools:<hash>             the load-test kit (../../kit) on alpine/k8s, tagged with
#                                             a hash of the kit's sources and tools.Dockerfile
#
# Bifrost and Portkey are not built or copied: the cluster pulls their published images by digest
# (bifrost.version, portkey.version).
#
# Env: REGISTRY, LITELLM_SRC (a clone of github.com/BerriAI/litellm; needed unless every LiteLLM
# image is already present), LITELLM_SHA, LTO (fat|off), SIDES, PLATFORM (default linux/amd64),
# PUSH (1, the default, pushes; 0 loads into the local Docker daemon instead, for a local cluster
# you load images into yourself),
# FORCE_BUILD=1 rebuilds images that are already present.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

check_inputs
PUSH="${PUSH:-1}"
FORCE_BUILD="${FORCE_BUILD:-0}"
[ -n "$REGISTRY" ] || die "set REGISTRY to a registry the cluster can pull from (and docker login to it); with PUSH=0 any prefix works, such as REGISTRY=local"
resolve_images

case "$LTO" in
  fat) LTO_ARGS=(--build-arg LTO=fat --build-arg CODEGEN_UNITS=1) ;;
  off) LTO_ARGS=(--build-arg LTO=off --build-arg CODEGEN_UNITS=16) ;;
esac

present() {
  [ "$FORCE_BUILD" = 1 ] && return 1
  if [ "$PUSH" = 1 ]; then
    docker buildx imagetools inspect "$1" > /dev/null 2>&1
  else
    docker image inspect "$1" > /dev/null 2>&1
  fi
}

build() {
  local ref="$1"
  shift
  local out=(--load)
  [ "$PUSH" = 1 ] && out=(--push)
  DOCKER_BUILDKIT=1 docker buildx build --platform "$PLATFORM" "${out[@]}" -t "$ref" "$@"
}

SRC=""
cleanup() {
  if [ -n "$SRC" ]; then git -C "$LITELLM_SRC" worktree remove --force "$SRC" > /dev/null 2>&1 || true; fi
}
trap cleanup EXIT

# A detached worktree of LITELLM_SRC at LITELLM_SHA, so the checkout itself is left as it is.
ensure_source() {
  [ -n "$SRC" ] && return 0
  [ -n "${LITELLM_SRC:-}" ] || die "set LITELLM_SRC to a clone of https://github.com/BerriAI/litellm"
  if ! git -C "$LITELLM_SRC" cat-file -e "${LITELLM_SHA}^{commit}" 2> /dev/null; then
    echo "--- fetching ${LITELLM_SHA} into ${LITELLM_SRC}"
    git -C "$LITELLM_SRC" fetch -q origin "$LITELLM_SHA"
  fi
  SRC="$(mktemp -d)/litellm"
  git -C "$LITELLM_SRC" worktree add -q --detach "$SRC" "$LITELLM_SHA"
  local f
  for f in litellm-rust/crates/gateway/Dockerfile gateway/Dockerfile migrations/Dockerfile; do
    [ -f "${SRC}/${f}" ] || die "${f} does not exist at ${LITELLM_SHA}; the lane needs a commit with the Rust gateway, the gateway image and the migrations image"
  done
}

if has_side rust; then
  echo "--- ${RUST_IMAGE}"
  if present "$RUST_IMAGE"; then
    echo "already present; nothing to do"
  else
    ensure_source
    echo "build (LTO=${LTO})"
    build "$RUST_IMAGE" "${LTO_ARGS[@]}" -f "${SRC}/litellm-rust/crates/gateway/Dockerfile" "${SRC}/litellm-rust"
  fi
fi

if has_side python; then
  echo "--- ${PYTHON_IMAGE}"
  if present "$PYTHON_IMAGE"; then
    echo "already present; nothing to do"
  else
    ensure_source
    build "$PYTHON_IMAGE" -f "${SRC}/gateway/Dockerfile" "$SRC"
  fi
fi

echo "--- ${MIGRATIONS_IMAGE}"
if present "$MIGRATIONS_IMAGE"; then
  echo "already present; nothing to do"
else
  ensure_source
  build "$MIGRATIONS_IMAGE" -f "${SRC}/migrations/Dockerfile" "$SRC"
fi

echo "--- ${TOOLS_IMAGE}"
if present "$TOOLS_IMAGE"; then
  echo "already present; nothing to do"
else
  build "$TOOLS_IMAGE" -f "${LANE_DIR}/tools.Dockerfile" "$KIT_DIR"
fi

echo
echo "images:"
has_side rust && echo "  RUST_IMAGE=${RUST_IMAGE}"
has_side python && echo "  PYTHON_IMAGE=${PYTHON_IMAGE}"
echo "  MIGRATIONS_IMAGE=${MIGRATIONS_IMAGE}"
echo "  TOOLS_IMAGE=${TOOLS_IMAGE}"
echo "  BIFROST_IMAGE=${BIFROST_IMAGE} (published, pulled by digest)"
echo "  PORTKEY_IMAGE=${PORTKEY_IMAGE} (published, pulled by digest)"
