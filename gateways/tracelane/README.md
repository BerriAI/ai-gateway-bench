# Tracelane

Tracelane (`tracelane.dev`) has no `POST /v1/messages` route — only
OpenAI-shaped `POST /v1/chat/completions` — so it cannot run this harness's
existing Anthropic-route scenarios unmodified. It CAN run the OpenAI-route
`scenarios/openai_overhead` scenario added alongside this file, on the same
mock every other gateway in the comparison uses.

Run self-host (single-tenant, no external control plane needed):

```bash
docker run --rm -p 8080:8080 \
  -e TRACELANE_SELF_HOST=1 \
  -e TRACELANE_MASTER_KEY=<random> \
  -e ANTHROPIC_API_KEY=bench \
  -e ANTHROPIC_BASE_URL=<this harness's mock URL> \
  -e NATS_URL=<a NATS JetStream URL — required, capture cannot be disabled> \
  -e CLICKHOUSE_URL=<a ClickHouse HTTP URL> \
  ghcr.io/tracelane/tracelane/gateway:latest
```

Point the OpenAI-route scenario at it with `GWBENCH_ENDPOINT=/v1/chat/completions`
and a model string that starts with `claude` (Tracelane's model→provider
catalog routes by literal prefix and fails closed on anything else), e.g.
`GWBENCH_MODEL=claude-mock-gwbench`.

**Known limitation, not a bug in this harness:** a **release-build** Tracelane
gateway's own SSRF hardening rejects a loopback or RFC1918
`ANTHROPIC_BASE_URL` (no debug-only bypass exists in release binaries, by
design). Point `ANTHROPIC_BASE_URL` at the mock's address as reached from a
genuinely public IP (or run a debug build with
`TRACELANE_SSRF_ALLOW_LOOPBACK_FOR_TESTS=1`, which is not a representative
overhead number — debug builds are materially slower than release).

Capture (NATS + ClickHouse) must stay on — Tracelane refuses to boot without
`NATS_URL` set (there is no flag to disable span capture entirely), and a
gateway measured with its own capture pipeline dropped would not be measuring
the product this harness is comparing.
