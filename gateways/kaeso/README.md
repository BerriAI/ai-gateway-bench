# Kaeso

[Kaeso](https://github.com/logicplanes/kaeso) is an open-source (MIT) AI gateway written in Rust. It keeps its
organizations, projects, keys and usage in PostgreSQL and its counters and caches in Valkey, so it runs with both.
Request logs go to ClickHouse when one is configured; the benchmark runs without it unless noted.

These steps start the tagged release `0.7.0` on Linux with host networking, pointed at the mock on port 9000, then
create an organization, a project that allows one model, and an API key through Kaeso's Platform API.

```bash
PW=$(openssl rand -hex 16)
docker run -d --name kaeso-pg --network host -e POSTGRES_USER=kaeso -e POSTGRES_PASSWORD=$PW -e POSTGRES_DB=kaeso \
  postgres:18.6-alpine@sha256:9ba53c7d41ea093baf5c1ba635ba5434c4f979880527d49bb7d9237d3193085b
docker run -d --name kaeso-vk --network host \
  valkey/valkey:9.1.3-alpine@sha256:bf09d58e96d6b4524a15396b65b242bbe18beebbcc15a04d8ee75ddac62fae0b \
  valkey-server --requirepass $PW --maxmemory 512mb --maxmemory-policy noeviction
docker run -d --name kaeso-gw --network host \
  -e KAESO_PUBLIC_URL=http://127.0.0.1:8080 \
  -e KAESO_POSTGRES_URL=postgres://kaeso:$PW@127.0.0.1:5432/kaeso \
  -e KAESO_VALKEY_URL=redis://:$PW@127.0.0.1:6379 \
  -e KAESO_MASTER_KEYS=k1:$(openssl rand -base64 32) \
  -e KAESO_MAIL_TRANSPORT=log \
  -e KAESO_PROVIDERS_ANTHROPIC_BASE_URL=http://127.0.0.1:9000 \
  ghcr.io/logicplanes/kaeso-gateway:0.7.0@sha256:3d32fd7827417e49475b4ac9ad7d25f7daed28f02fa489ef4077ff501faaae1a
KAESO_PASSWORD=$PW python gateways/kaeso/setup.py
```

`setup.py` waits for `/readyz`, signs in the first user with the setup link `kaeso setup-link` prints, and prints the
key. Every request then goes to Kaeso's Anthropic Messages route with that key and a model the project allows:

```bash
curl -s http://127.0.0.1:8080/v1/messages \
  -H "Authorization: Bearer $KAESO_KEY" -H 'content-type: application/json' \
  -d '{"model": "claude-haiku-4-5", "max_tokens": 40, "messages": [{"role": "user", "content": "hi"}]}'
```

For `load-probe`, pass `--url http://127.0.0.1:8080/v1/messages --model claude-haiku-4-5 --header "Authorization: Bearer $KAESO_KEY"`.

On each request Kaeso checks the project's API key and that the project allows the model, then calls the mock in the
Anthropic Messages format and records usage in PostgreSQL. To also write every request to ClickHouse, start
`clickhouse/clickhouse-server` (its native port moved off 9000, which the mock uses) and set
`KAESO_CLICKHOUSE_URL=http://kaeso:<password>@127.0.0.1:8123` on the gateway.

Results from one run of every local-lane gateway with Kaeso added are in
[`results/kaeso-local-2026-11-22`](../../results/kaeso-local-2026-11-22).
