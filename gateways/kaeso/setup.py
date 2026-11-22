"""Create what a benchmark run needs in a fresh Kaeso: the first user and organization, a project that allows
claude-haiku-4-5 from the Anthropic provider (pointed at the mock by KAESO_PROVIDERS_ANTHROPIC_BASE_URL), and an API
key for it. Prints `export KAESO_KEY=...`.

Usage: KAESO_PASSWORD=... python gateways/kaeso/setup.py [--container kaeso-gw] [--url http://127.0.0.1:8080]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="kaeso-gw")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default="claude-haiku-4-5")
    args = parser.parse_args()
    password = os.environ.get("KAESO_PASSWORD") or os.urandom(16).hex()

    deadline = time.time() + 180
    while True:
        try:
            if httpx.get(f"{args.url}/readyz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.time() > deadline:
            raise SystemExit(f"{args.url}/readyz did not answer 200")
        time.sleep(0.5)

    # The setup link is printed to stderr.
    link = subprocess.run(["docker", "exec", args.container, "kaeso", "setup-link"], capture_output=True, text=True)
    match = re.search(r"token=([A-Za-z0-9_.~-]+)", link.stdout + link.stderr)
    if not match:
        raise SystemExit(f"no setup token in: {(link.stdout + link.stderr)[:500]}")

    base = f"{args.url}/platform/v1"
    with httpx.Client(timeout=30, headers={"Origin": args.url}) as c:
        c.post(f"{base}/auth/setup", json={
            "token": match.group(1), "email": "owner@example.com", "name": "Bench",
            "org_name": "Bench", "password": password,
        }).raise_for_status()
        org = c.get(f"{base}/auth/session").json()["memberships"][0]["org_id"]
        made = c.post(f"{base}/orgs/{org}/projects", json={"name": "Bench"})
        made.raise_for_status()
        project = made.json()["id"]
        c.post(f"{base}/orgs/{org}/credentials", json={"provider": "anthropic", "name": "mock", "secret": "sk-mock-anthropic"}).raise_for_status()
        c.post(f"{base}/orgs/{org}/projects/{project}/models", json={"provider": "anthropic", "model_id": args.model}).raise_for_status()
        key = c.post(f"{base}/orgs/{org}/projects/{project}/keys", json={"name": "bench"})
        key.raise_for_status()
    print(f"export KAESO_KEY={key.json()['secret']}")


if __name__ == "__main__":
    main()
