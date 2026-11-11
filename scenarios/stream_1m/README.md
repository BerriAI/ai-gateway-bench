# 1M-token streams

Every request streamed with a near-1M-token body (about 4 MB): `/v1/messages` with 950k to 1M
tokens and `/v1/chat/completions` with 870k to 920k (under the chat model's 922k window). Time to
first token 1 to 3 s, streams of 5 to 60 s, at 0.5, 1, 2, 4 and 8 requests per second, 3 minutes
each. At 8 rps about 260 streams are open at once. LiteLLM Rust, Bifrost and Portkey, basic
features. It shows first-byte time and peak memory at the largest context.

- Workload: [`kit/workloads/stream-1m.json`](../../kit/workloads/stream-1m.json).

```sh
cd lanes/k8s
SIDES=rust-bifrost-portkey FEATURES=basic ROUNDS=1 WORKLOADS=stream-1m ./bench.sh all
```

There is no direct-to-mock floor at this body size, so first byte includes moving 4 MB over two
hops. Rates are nominal; Poisson arrivals made the measured rates 0.5, 1.1, 2.3, 3.8 and 7.7.
The published values are in [`results/better-benchmarks/big-bodies.csv`](../../results/better-benchmarks/big-bodies.csv).
