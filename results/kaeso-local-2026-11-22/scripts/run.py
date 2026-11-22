"""Run BerriAI/ai-gateway-bench's local lane for every gateway, Kaeso included.

Gateways run one at a time as containers on host networking, pinned to cores
GW_CPUS. The mock, the load drivers and gateway dependencies (Postgres, Valkey,
ClickHouse) run on DRIVER_CPUS. Every measurement uses the bench's own drivers
unchanged (load-probe, tools/run_http_bench.py, agents/session_runner.py) and is
paired with a direct-to-mock run of the same shape taken right before it.
"""

from __future__ import annotations

import base64
import json
import os
import random
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil

BENCH = Path("/root/ai-gateway-bench")
OUT = Path("/root/out")
GW_CPUS = "2,3"
DRIVER_CPUS = "0,1"
MOCK = "http://127.0.0.1:9000"
ROUNDS = int(os.environ.get("ROUNDS", "3"))
PY = str(BENCH / ".venv/bin/python")
PROBE = str(BENCH / "target/release/load-probe")
SWEEP = [(1, 1000), (4, 2000), (16, 4000), (64, 16000)]
CFG = Path("/root/cfg")


def sh(cmd: str, check: bool = True) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{cmd}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def pinned(args: list[str]) -> list[str]:
    return ["taskset", "-c", DRIVER_CPUS, *args]


def docker_rm(*names: str) -> None:
    for name in names:
        sh(f"docker rm -f {name} >/dev/null 2>&1", check=False)


def wait_http(url: str, timeout: float = 180, ok=lambda r: r.status_code < 500) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2)
            if ok(r):
                return
            last = r.status_code
        except httpx.HTTPError as e:
            last = e
        time.sleep(0.5)
    raise RuntimeError(f"{url} not ready: {last}")


class Sampler:
    """CPU and RSS of a container's whole process tree, sampled every 0.25 s."""

    def __init__(self, container: str):
        self.root = psutil.Process(int(sh(f"docker inspect -f '{{{{.State.Pid}}}}' {container}")))
        self.samples: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self._procs: dict[int, psutil.Process] = {}

    def _tree(self) -> list[psutil.Process]:
        procs = [self.root, *self.root.children(recursive=True)]
        out = []
        for p in procs:
            known = self._procs.get(p.pid)
            if known is None:
                known = p
                known.cpu_percent(None)
                self._procs[p.pid] = known
            out.append(known)
        return out

    def rss_mb(self) -> float:
        total = 0
        for p in self._tree():
            try:
                total += p.memory_info().rss
            except psutil.Error:
                pass
        return total / 1_000_000

    def _run(self) -> None:
        self._tree()
        while not self._stop.wait(0.25):
            cpu = rss = 0.0
            for p in self._tree():
                try:
                    cpu += p.cpu_percent(None)
                    rss += p.memory_info().rss
                except psutil.Error:
                    pass
            self.samples.append((cpu, rss / 1_000_000))

    def __enter__(self):
        self.samples = []
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()

    def summary(self) -> dict:
        busy = [c for c, _ in self.samples]
        return {
            "avg_cpu_percent": sum(busy) / len(busy) if busy else 0,
            "peak_rss_mb": max((r for _, r in self.samples), default=0),
            "samples": len(self.samples),
        }


# ---------------------------------------------------------------- gateways


def start_litellm_rust() -> dict:
    docker_rm("gw")
    sh(
        f"docker run -d --name gw --network host --cpuset-cpus {GW_CPUS} "
        f"-e LITELLM_CONFIG=/app/config.yaml -e LITELLM_MASTER_KEY=sk-gwbench "
        f"-e HOST=127.0.0.1 -e PORT=8101 -v {CFG}/litellm-rust.yaml:/app/config.yaml:ro "
        f"-v /root/litellm/litellm/rust/target/release/litellm-gateway:/usr/local/bin/litellm-gateway:ro "
        f"--user 10001:10001 --entrypoint /usr/local/bin/litellm-gateway litellm-rust-runtime"
    )
    wait_http("http://127.0.0.1:8101/health/readiness", ok=lambda r: True)
    return {
        "url": "http://127.0.0.1:8101/v1/messages",
        "model": "anthropic/mock",
        "headers": ["Authorization: Bearer sk-gwbench"],
        "containers": ["gw"],
    }


def start_litellm_python() -> dict:
    docker_rm("gw")
    sh(
        f"docker run -d --name gw --network host --cpuset-cpus {GW_CPUS} "
        f"-e LITELLM_MASTER_KEY=sk-gwbench -v {CFG}/litellm-python.yaml:/app/config.yaml:ro "
        f"ghcr.io/berriai/litellm:v1.110.2 --config /app/config.yaml --port 8102 --host 127.0.0.1"
    )
    wait_http("http://127.0.0.1:8102/health/readiness", timeout=300, ok=lambda r: r.status_code == 200)
    return {
        "url": "http://127.0.0.1:8102/v1/messages",
        "model": "anthropic/mock",
        "headers": ["Authorization: Bearer sk-gwbench"],
        "containers": ["gw"],
    }


def start_bifrost() -> dict:
    docker_rm("gw")
    sh(
        f"docker run -d --name gw --network host --cpuset-cpus {GW_CPUS} "
        f"-e APP_PORT=8103 -e APP_HOST=127.0.0.1 --tmpfs /app/data:mode=1777 "
        f"-v {CFG}/bifrost.json:/cfg/config.json:ro --entrypoint sh maximhq/bifrost:v2.6.0 "
        f"-c 'cp /cfg/config.json /app/data/config.json && exec /app/docker-entrypoint.sh /app/main'"
    )
    wait_http("http://127.0.0.1:8103/health", ok=lambda r: r.status_code == 200)
    return {
        "url": "http://127.0.0.1:8103/anthropic/v1/messages",
        "model": "mock/mock",
        "headers": [],
        "containers": ["gw"],
    }


def start_portkey() -> dict:
    docker_rm("gw")
    sh(f"docker run -d --name gw --network host --cpuset-cpus {GW_CPUS} portkeyai/gateway:1.15.2")
    wait_http("http://127.0.0.1:8787/", ok=lambda r: r.status_code == 200)
    return {
        "url": "http://127.0.0.1:8787/v1/messages",
        "model": "mock",
        "headers": [
            "x-portkey-provider: anthropic",
            f"x-portkey-custom-host: {MOCK}/v1",
            "x-api-key: dummy",
        ],
        "containers": ["gw"],
    }


PG = "postgres:18.6-alpine@sha256:9ba53c7d41ea093baf5c1ba635ba5434c4f979880527d49bb7d9237d3193085b"
VALKEY = "valkey/valkey:9.1.3-alpine@sha256:bf09d58e96d6b4524a15396b65b242bbe18beebbcc15a04d8ee75ddac62fae0b"
CLICKHOUSE = "clickhouse/clickhouse-server:26.3.62.3-alpine@sha256:1d45e52080e6ed136a6377863046a0e53d69fb0418d799f12e23184ed6460c55"


def start_kaeso(logs: bool = False) -> dict:
    docker_rm("gw", "kpg", "kvk", "kch")
    pw = secrets.token_hex(16)
    deps = f"--network host --cpuset-cpus {DRIVER_CPUS}"
    sh(f"docker run -d --name kpg {deps} -e POSTGRES_USER=kaeso -e POSTGRES_PASSWORD={pw} -e POSTGRES_DB=kaeso {PG}")
    sh(f"docker run -d --name kvk {deps} {VALKEY} valkey-server --requirepass {pw} --maxmemory 512mb --maxmemory-policy noeviction")
    if logs:
        sh(
            f"docker run -d --name kch {deps} --ulimit nofile=262144:262144 -v {CFG}/ch-ports.xml:/etc/clickhouse-server/config.d/zz-ports.xml:ro -e CLICKHOUSE_USER=kaeso "
            f"-e CLICKHOUSE_PASSWORD={pw} -e CLICKHOUSE_DB=kaeso {CLICKHOUSE}"
        )
    for _ in range(120):
        if subprocess.run(f"docker exec kpg pg_isready -h 127.0.0.1 -U kaeso -d kaeso", shell=True, capture_output=True).returncode == 0:
            break
        time.sleep(0.5)
    if logs:
        wait_http("http://127.0.0.1:8123/ping", ok=lambda r: r.status_code == 200)
    master = base64.b64encode(secrets.token_bytes(32)).decode()
    ch = f"http://kaeso:{pw}@127.0.0.1:8123" if logs else ""
    sh(
        f"docker run -d --name gw --network host --cpuset-cpus {GW_CPUS} "
        f"-e KAESO_PUBLIC_URL=http://127.0.0.1:8080 "
        f"-e KAESO_POSTGRES_URL=postgres://kaeso:{pw}@127.0.0.1:5432/kaeso "
        f"-e KAESO_VALKEY_URL=redis://:{pw}@127.0.0.1:6379 "
        f"-e KAESO_CLICKHOUSE_URL={ch} -e KAESO_MASTER_KEYS=k1:{master} "
        f"-e KAESO_MAIL_TRANSPORT=log -e KAESO_LOG_FORMAT=json "
        f"-e KAESO_PROVIDERS_ANTHROPIC_BASE_URL={MOCK} -e KAESO_PROVIDERS_OPENAI_BASE_URL={MOCK} "
        f"ghcr.io/logicplanes/kaeso-gateway:0.7.0"
    )
    wait_http("http://127.0.0.1:8080/readyz", ok=lambda r: r.status_code == 200)
    import re
    link = sh("docker exec gw kaeso setup-link 2>&1")
    match = re.search(r"token=([A-Za-z0-9_.~-]+)", link)
    if not match:
        raise RuntimeError(f"no setup token in: {link[:500]}")
    token = match.group(1)
    base = "http://127.0.0.1:8080/platform/v1"
    origin = {"Origin": "http://127.0.0.1:8080"}
    with httpx.Client(timeout=30, headers=origin) as c:
        c.post(f"{base}/auth/setup", json={
            "token": token, "email": "owner@example.com", "name": "Bench",
            "org_name": "Bench", "password": pw,
        }).raise_for_status()
        org = c.get(f"{base}/auth/session").json()["memberships"][0]["org_id"]
        made = c.post(f"{base}/orgs/{org}/projects", json={"name": "Bench"})
        made.raise_for_status()
        project = made.json()["id"]
        c.post(f"{base}/orgs/{org}/credentials", json={"provider": "anthropic", "name": "mock", "secret": "sk-mock-anthropic"}).raise_for_status()
        c.post(f"{base}/orgs/{org}/projects/{project}/models", json={"provider": "anthropic", "model_id": "claude-haiku-4-5"}).raise_for_status()
        key = c.post(f"{base}/orgs/{org}/projects/{project}/keys", json={"name": "bench"})
        key.raise_for_status()
        api_key = key.json()["secret"]
    return {
        "url": "http://127.0.0.1:8080/v1/messages",
        "model": "claude-haiku-4-5",
        "headers": [f"Authorization: Bearer {api_key}"],
        "containers": ["gw"],
        "cleanup": ["kpg", "kvk", "kch"],
    }


GATEWAYS = {
    "kaeso": start_kaeso,
    "kaeso-logs": lambda: start_kaeso(logs=True),
    "litellm-rust": start_litellm_rust,
    "litellm-python": start_litellm_python,
    "bifrost": start_bifrost,
    "portkey": start_portkey,
}

DIRECT = {"url": f"{MOCK}/v1/messages", "model": "anthropic/mock", "headers": []}


# ---------------------------------------------------------------- measurements


def probe(target: dict, label: str, concurrency: int, count: int, out: Path) -> dict:
    args = [PROBE, "--label", label, "--url", target["url"], "--count", str(count),
            "--concurrency", str(concurrency), "--model", target["model"], "--output", str(out)]
    for h in target["headers"]:
        args += ["--header", h]
    subprocess.run(pinned(args), check=True, capture_output=True)
    return json.loads(out.read_text())


def stream(target: dict, label: str, out: Path) -> dict:
    args = [PY, "tools/run_http_bench.py", "--label", label, "--url", target["url"], "--endpoint", "anthropic",
            "--model", target["model"], "--output", str(out), "--count", "200", "--stream",
            "--concurrency", "16", "--expected-chunks", "45"]
    for h in target["headers"]:
        args += ["--header", h]
    subprocess.run(pinned(args), check=True, capture_output=True, cwd=BENCH)
    return json.loads(out.read_text())


def session(target: dict, profile: str, streaming: bool, out: Path) -> dict:
    args = [PY, "agents/session_runner.py", "--profile", profile, "--url", target["url"],
            "--model", target["model"], "--output", str(out)]
    if not streaming:
        args.append("--non-stream")
    for h in target["headers"]:
        args += ["--header", h]
    subprocess.run(pinned(args), check=True, capture_output=True, cwd=BENCH)
    return json.loads(out.read_text())


def suite(name: str, target: dict, rnd: int, sampler: Sampler | None) -> dict:
    d = OUT / f"r{rnd}" / name
    d.mkdir(parents=True, exist_ok=True)
    res: dict = {"sweep": {}, "stream": None, "sessions": {}}
    probe(target, f"{name}-warmup", 4, 400, d / "warmup.json")
    if sampler:
        res["idle_rss_mb"] = sampler.rss_mb()
    for conc, count in SWEEP:
        if sampler:
            with sampler:
                r = probe(target, f"{name}-c{conc}", conc, count, d / f"c{conc}.json")
            r["resources"] = sampler.summary()
        else:
            r = probe(target, f"{name}-c{conc}", conc, count, d / f"c{conc}.json")
        r["error_count"] = len(r.pop("errors"))
        res["sweep"][conc] = r
    try:
        if sampler:
            with sampler:
                s = stream(target, f"{name}-stream", d / "stream.json")
            s["resources"] = sampler.summary()
        else:
            s = stream(target, f"{name}-stream", d / "stream.json")
        s["error_count"] = len(s.get("errors", []))
        s["error_sample"] = s.pop("errors", [])[:3]
        res["stream"] = s
    except subprocess.CalledProcessError as e:
        res["stream"] = {"failed": e.stderr.decode()[-500:] if e.stderr else str(e)}
    for profile in ("claude_code", "codex"):
        for streaming in (False, True):
            key = f"{profile}_{'stream' if streaming else 'nonstream'}"
            try:
                r = session(target, profile, streaming, d / f"session_{key}.json")
                res["sessions"][key] = {
                    "total_wall_time_ms": r["total_wall_time_ms"],
                    "error_count": len(r["errors"]),
                    "invalid_tool_turns": len(r["invalid_tool_turns"]),
                    "error_sample": r["errors"][:2],
                }
            except subprocess.CalledProcessError as e:
                res["sessions"][key] = {"failed": (e.stderr.decode() if e.stderr else str(e))[-500:]}
    if sampler:
        res["end_rss_mb"] = sampler.rss_mb()
    return res


def main() -> None:
    names = sys.argv[1:] or list(GATEWAYS)
    OUT.mkdir(exist_ok=True)
    sh("pkill -f mock-upstream", check=False)
    mock = subprocess.Popen(pinned([str(BENCH / "target/release/mock-upstream")]),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_http(f"{MOCK}/health")
    results_path = OUT / "results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    try:
        for rnd in range(1, ROUNDS + 1):
            order = names[:]
            random.Random(rnd).shuffle(order)
            for name in order:
                key = f"r{rnd}:{name}"
                if key in results:
                    continue
                print(f"[{time.strftime('%H:%M:%S')}] round {rnd} {name}: direct", flush=True)
                direct = suite("direct", DIRECT, rnd, None)
                print(f"[{time.strftime('%H:%M:%S')}] round {rnd} {name}: gateway", flush=True)
                target = GATEWAYS[name]()
                try:
                    sampler = Sampler(target["containers"][0])
                    gw = suite(name, target, rnd, sampler)
                finally:
                    sh(f"docker logs --tail 200 gw > {OUT}/r{rnd}/{name}/gateway.log 2>&1", check=False)
                    docker_rm("gw", *target.get("cleanup", []))
                results[key] = {"gateway": gw, "direct": direct}
                results_path.write_text(json.dumps(results, indent=1))
                c16 = gw["sweep"][16]
                print(f"   c16 p50 {c16['latency_p50_ms']:.2f} p99 {c16['latency_p99_ms']:.2f} "
                      f"(direct {direct['sweep'][16]['latency_p50_ms']:.2f}/{direct['sweep'][16]['latency_p99_ms']:.2f}) "
                      f"errors {c16['error_count']}", flush=True)
    finally:
        mock.terminate()


if __name__ == "__main__":
    main()
