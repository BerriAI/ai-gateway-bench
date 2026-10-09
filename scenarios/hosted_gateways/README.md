# Hosted gateways (real model)

The rest of this benchmark points every gateway at a local deterministic mock so only the gateway's own overhead remains. That isolates proxy cost, but it cannot include hosted gateways: OpenRouter cannot be pointed at a local mock. This scenario measures the same overhead against a real model instead, with the hosted arm in the same shuffled run so upstream drift hits every arm equally

## Arms

- `direct`: https://api.anthropic.com/v1/messages, model `claude-haiku-4-5`
- `litellm-rust`: LiteLLM Rust gateway (`litellm-rust/target/release/litellm-gateway`) on 127.0.0.1:4100, alias `claude-haiku-4.5` -> `anthropic/claude-haiku-4-5`
- `litellm-python`: LiteLLM Python proxy v1.106.0 (1 worker, no DB) on 127.0.0.1:4010, same alias
- `openrouter`: https://openrouter.ai/api/v1/messages, model `anthropic/claude-haiku-4.5`, `provider: {order: ["anthropic"], allow_fallbacks: false}` so every generation is served by Anthropic

Both LiteLLM builds are from BerriAI/litellm commit `72ab736863` and share `litellm.yaml` in this folder with master key `sk-bench`. Each gateway process is pinned to one vCPU with `taskset`

## Method

Streaming `/v1/messages`, prompt "Count from 1 to 10.", `max_tokens` 64. 300 requests per arm, all 1,200 shuffled across arms with seed 0, 2 concurrent client threads, one warm persistent httpx client per arm (keepalive 600 s), 3 warm-up calls per arm. TTFT is the first `text_delta` event, not the first byte: direct Anthropic and OpenRouter send `message_start` within about 50 ms, so a first-byte metric would understate gateway overhead

The Python proxy closed client connections, so its arm opened 26 new connections during the run (every other arm opened 1). That cost is included in its numbers because it is gateway behavior

## Running it

```bash
export ANTHROPIC_API_KEY=...   # direct arm and upstream for both LiteLLM gateways
export OPENROUTER_API_KEY=...  # openrouter arm

# 1. start both gateways with the shared config
LITELLM_CONFIG=scenarios/hosted_gateways/litellm.yaml \
  litellm-rust/target/release/litellm-gateway   # 127.0.0.1:4100
litellm --config scenarios/hosted_gateways/litellm.yaml --port 4010 --num_workers 1

# 2. run the shuffled benchmark
python scenarios/hosted_gateways/run.py --out results/hosted_messages_raw.jsonl

# 3. fetch OpenRouter's own per-generation metadata (provider, upstream latency)
python scenarios/hosted_gateways/openrouter_generation.py \
  results/hosted_messages_raw.jsonl results/openrouter_generation.jsonl

# 4. build the CSVs the chart reads
python tools/build_hosted_csv.py

# 5. regenerate the chart
python analyze/make_hosted_chart.py
```

`tools/build_hosted_csv.py` needs `numpy` (see `requirements.txt`)

## Recorded run

Run date 2026-10-09 on an AWS KVM VM (Intel Xeon Platinum 8559C, 8 vCPU, 31 GiB, kernel 6.8.0-1061-aws). OpenRouter-billed cost was $0.042 for 300 calls; the whole run stayed under $0.20. Every OpenRouter generation was confirmed served by Anthropic via the generation metadata (`results/openrouter_generation.jsonl`)

"OpenRouter own overhead" in `results/openrouter_overhead.csv` is client TTFT minus OpenRouter's self-reported `latency` field from its generation API. OpenRouter does not formally define that field, so treat it as an estimate
