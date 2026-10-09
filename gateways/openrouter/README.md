# OpenRouter

Hosted gateway; there is no local process to start. It is measured only in the real-model scenario (`scenarios/hosted_gateways/`), never against the local mock, because a hosted gateway cannot be pointed at a mock upstream

- Endpoint: `https://openrouter.ai/api/v1/messages`
- Model: `anthropic/claude-haiku-4.5`
- Provider pin: `provider: {order: ["anthropic"], allow_fallbacks: false}` so requests are served by Anthropic and the arm is comparable with the direct arm
- Key: `OPENROUTER_API_KEY` environment variable, sent as `Authorization: Bearer`

It is absent from the mock-based scenarios and their charts for the same reason it needs the real-model scenario: every mock scenario assumes the gateway can route to `127.0.0.1`, which a hosted service cannot reach
