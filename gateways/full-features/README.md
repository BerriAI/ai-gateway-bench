# Full-feature gateway configs

These are the configs the Kubernetes lane ([`lanes/k8s`](../../lanes/k8s)) gives each gateway for
the full-feature runs. They are generated, not hand-written. This copy was made with:

```sh
cd kit
go run ./cmd/genconfig -out out -mocks http://mockllm:9000 \
  -chat-groups 10 -messages-groups 10 -deployments 4 -bedrock-fraction 0.5 \
  -keys 1000 -key-seed reference -master-key sk-bench-not-a-secret \
  -features full -key-sets python,rust,bifrost,portkey -bifrost-bedrock-tls mockllm:9443
```

The lane runs the same command with its own mock address and a key seed per run, so model
groups, deployments, limits and callbacks match these files and only the keys differ.

| File | Used by |
|---|---|
| `config.yaml` | LiteLLM Rust and LiteLLM Python. 20 model groups (10 chat, 10 messages) of 4 deployments, half of each messages group on Bedrock invoke. Router: simple-shuffle, 2 retries, 3 allowed fails, 30 s cooldown. Callbacks `dynamic_rate_limiter_v3`, `prometheus`, `s3_v2`. The Rust gateway runs the four request hooks under `litellm_settings.hooks`; Python runs the stand-ins in `custom_callbacks.py`. |
| `custom_callbacks.py` | LiteLLM Python: forward, add and strip headers, and attach spend metadata, matching the Rust hooks. |
| `headers-*.txt` | loadgen: the request headers each side's hooks act on, one `-header` per line. |
| `portkey-headers.json` | loadgen for Portkey: the `x-portkey-config` routing header per model group (load balance over the same 4 deployments, 2 retries). |

Not committed, because they hold the generated keys and are large: `keys-*.txt`, `keys.sql`
(virtual keys, users, teams and members with rpm, tpm and budgets, loaded into Postgres) and
`bifrost-config.json` / `bifrost-pricing.json` (the same keys, teams, budgets and limits mapped onto
Bifrost's governance config). Run the command above to get them.

## What "full features" turns on

| Feature | LiteLLM Rust | LiteLLM Python | Bifrost | Portkey |
|---|---|---|---|---|
| Virtual keys (1,000 per gateway) | yes | yes | yes | no |
| Key, user, team and team member budgets | yes | yes | key, team, customer (users and members are enterprise-only) | no |
| rpm and tpm limits on keys, users, teams, members | yes | yes | key, team, customer | no |
| `max_parallel_requests` per key | yes | yes | no | no |
| rpm and tpm per deployment | yes | yes | yes (model configs) | no |
| Dynamic rate limiter (`dynamic_rate_limiter_v3`) | yes | yes | no | no |
| Spend logs, key, team and user spend, daily tables in Postgres | yes | yes | its own logs store and budgets | no |
| Prometheus metrics | yes | yes | yes (telemetry plugin) | no |
| S3 request logs | yes (`s3_v2`) | yes (`s3_v2`) | yes (logs store payloads) | no |
| Forward and strip request headers | yes (hooks) | yes (custom callbacks) | yes (`header_filter_config`) | no |
| Spend metadata from request headers | yes (hook) | yes (custom callback) | yes (`logging_headers`) | no |
| Add the key's team as an upstream header | yes (hook) | yes (custom callback) | no | no |
| Router: shuffle over 4 deployments, 2 retries | yes | yes | yes (no cooldown) | yes (load balance, 2 retries) |
| Router cooldown (3 allowed fails, 30 s) | yes | yes | no | no |

Every limit and budget is set far above the test's load, so every check runs and nothing is
refused. Portkey's open-source gateway has no auth, keys, limits, budgets, logs or metrics, so it
runs the same routing without them.
