from __future__ import annotations

import os

from locust import HttpUser, between, task

from common.payloads import openai_body


class OpenAIOverheadUser(HttpUser):
    """PLT-23: gateway-overhead on the OpenAI chat-completions route.

    AIGatewayBench's scenarios all target Anthropic's `/v1/messages`. Tracelane
    has no such route (only OpenAI-shaped `/v1/chat/completions`), and every
    gateway under comparison also speaks OpenAI chat completions, so this
    variant drives the SAME overhead measurement — a non-streaming round trip
    through the gateway to the deterministic mock and back — on that route
    instead, for every gateway in the comparison including the direct
    (gateway-less) baseline. Same mock, same body shape, same `-u 16 -r 16
    -t 30s` parameters as the project's published post.
    """

    wait_time = between(0.05, 0.15)
    host = os.getenv("GWBENCH_TARGET", "http://127.0.0.1:8000")
    endpoint = os.getenv("GWBENCH_ENDPOINT", "/v1/chat/completions")
    model = os.getenv("GWBENCH_MODEL", "mock")

    @staticmethod
    def _extra_headers() -> dict[str, str]:
        # "k1:v1|k2:v2" — used for gateways that route by header rather than by
        # model string or path (Portkey: x-portkey-provider / x-portkey-custom-host).
        raw = os.getenv("GWBENCH_EXTRA_HEADERS", "")
        headers: dict[str, str] = {}
        for pair in filter(None, raw.split("|")):
            name, _, value = pair.partition(":")
            if name:
                headers[name.strip()] = value.strip()
        return headers

    @task
    def overhead_turn(self) -> None:
        headers = {"Authorization": f"Bearer {os.getenv('GWBENCH_API_KEY', 'gwbench')}"}
        headers.update(self._extra_headers())
        self.client.post(
            self.endpoint,
            json=openai_body(model=self.model, stream=False),
            headers=headers,
            name="openai_overhead",
        )
