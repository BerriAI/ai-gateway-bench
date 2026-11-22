#!/usr/bin/env bash
# Prepares a fresh Ubuntu 24.04 machine with four cores, as root, the way the 2026-11-22 run was prepared:
# Docker, BerriAI/ai-gateway-bench and LiteLLM Rust at the same commits, every image pulled by the digest the run used,
# and the scripts and configs in /root where run.py and run_capacity.py expect them.
set -euxo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BENCH_COMMIT=44365ca99307c2fde47c7ca0aa5e17c1665dcf38
LITELLM_COMMIT=d4e8f96b88c5113509b318b4d35520bf73e88152
RUST_IMAGE=rust:1.98.0-bookworm@sha256:82150a52ec202c1b14d7817e14516c392bb7f5cfebd88f1ed531cb37ebd39922

export DEBIAN_FRONTEND=noninteractive
# A machine that just booted is still running cloud-init and its first package updates: wait for both.
command -v cloud-init >/dev/null && cloud-init status --wait >/dev/null || true
APT="apt-get -o DPkg::Lock::Timeout=900"
$APT update -q
$APT install -yq docker.io python3-venv python3-pip build-essential pkg-config libssl-dev git jq curl util-linux
systemctl enable --now docker

command -v cargo >/dev/null || curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
. "$HOME/.cargo/env"

cd /root
[ -d ai-gateway-bench ] || git clone -q https://github.com/BerriAI/ai-gateway-bench.git
git -C ai-gateway-bench checkout -q "$BENCH_COMMIT"
(
  cd ai-gateway-bench
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  cargo build --release -q -p mock-upstream -p load-probe
)

# Every image by its digest, tagged with the name the scripts use.
while read -r tag digest; do
  docker pull -q "$digest"
  docker tag "$digest" "$tag"
done <<'IMAGES'
ghcr.io/logicplanes/kaeso-gateway:0.7.0 ghcr.io/logicplanes/kaeso-gateway@sha256:3d32fd7827417e49475b4ac9ad7d25f7daed28f02fa489ef4077ff501faaae1a
ghcr.io/berriai/litellm:v1.110.2 ghcr.io/berriai/litellm@sha256:ef798594ca3db8d81bad2e1439f536f49b71fe095ee9f20416f5e149b4e22035
maximhq/bifrost:v2.6.0 maximhq/bifrost@sha256:5968d007ba4f74ee01bc4ceb071decb9927a0b7bb43dbb9072208f75e2d522e3
portkeyai/gateway:1.15.2 portkeyai/gateway@sha256:97f094d9c8a764cbfaa2a7138c0017b247ca923bb06db1b4c13b7f8a33b5200d
IMAGES
# Kaeso's databases: run.py names them by digest already.
docker pull -q postgres:18.6-alpine@sha256:9ba53c7d41ea093baf5c1ba635ba5434c4f979880527d49bb7d9237d3193085b
docker pull -q valkey/valkey:9.1.3-alpine@sha256:bf09d58e96d6b4524a15396b65b242bbe18beebbcc15a04d8ee75ddac62fae0b
docker pull -q clickhouse/clickhouse-server:26.3.62.3-alpine@sha256:1d45e52080e6ed136a6377863046a0e53d69fb0418d799f12e23184ed6460c55

# LiteLLM Rust has no release image yet, and its Dockerfile at this commit embeds files from outside its build context,
# so the gateway is compiled in Rust's image with the whole repository mounted, then run in a slim image.
[ -d litellm ] || git clone -q https://github.com/BerriAI/litellm.git litellm
git -C litellm checkout -q "$LITELLM_COMMIT"
docker run --rm -v /root/litellm:/src -v /root/cargo-registry:/usr/local/cargo/registry -w /src/litellm/rust "$RUST_IMAGE" \
  sh -c "apt-get update -qq && apt-get install -y -qq cmake >/dev/null && cargo build --release --locked --package litellm-gateway --bin litellm-gateway"
sha256sum /root/litellm/litellm/rust/target/release/litellm-gateway
docker build -q -t litellm-rust-runtime -f "$HERE/Dockerfile.litellm-rust-runtime" "$HERE"

# The scripts and configs where they expect to be.
cp "$HERE"/run.py "$HERE"/run_capacity.py "$HERE"/analyze.py "$HERE"/analyze_cap.py /root/
mkdir -p /root/cfg && cp "$HERE"/cfg/* /root/cfg/
echo "Ready. Run: cd /root && ai-gateway-bench/.venv/bin/python run.py && ai-gateway-bench/.venv/bin/python run_capacity.py"
