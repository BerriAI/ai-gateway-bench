# Full-feature rate sweep

LiteLLM Rust, LiteLLM Python, Bifrost and Portkey at 10, 25, 50, 75 and 100 requests per second,
5 minutes per rate, with every gateway feature on (Portkey has none). It produces the overhead,
CPU and memory curves and the 50 rps headline numbers.

- Workloads: [`kit/workloads/sweep-*.json`](../../kit/workloads). Evenly spaced arrivals, half
  `/v1/chat/completions` and half `/v1/messages`, half of each streamed, 10k to 50k token contexts
  (log-uniform), time to first token 200 to 800 ms, 1 to 5 s per request, over 20 model groups.
- Gateway configs: [`gateways/full-features`](../../gateways/full-features).
- Every gateway gets the same requests in the same order (same seed), one after the other, on its
  own 4-core, 16 GiB pod.
- After the runs, `verify` checks each LiteLLM gateway's and Bifrost's spend logs, key, team and
  user spend, daily tables, Prometheus counters, S3 logs and forwarded headers against what was
  sent.

Run it with the Kubernetes lane ([`lanes/k8s`](../../lanes/k8s)):

```sh
cd lanes/k8s
SIDES=all-portkey FEATURES=full ROUNDS=1 \
  WORKLOADS=sweep-10,sweep-25,sweep-50,sweep-75,sweep-100 ./bench.sh all
```

Overhead in the lane's tables is observed time minus the time the mock was told to take. Subtract
the [direct-to-mock floor](../direct_floor) at the same rate and percentile to get what the gateway
alone adds. The published values are in
[`results/better-benchmarks/sweep.csv`](../../results/better-benchmarks/sweep.csv) and
[`headline.csv`](../../results/better-benchmarks/headline.csv).
