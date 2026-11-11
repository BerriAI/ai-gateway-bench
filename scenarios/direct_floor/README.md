# Direct-to-mock floor

The load generator sends the sweep's requests straight to the mock, with no gateway. What it
measures (client, network and mock) is part of every gateway's observed overhead, so it is
subtracted from each gateway's numbers at the same rate and percentile.

```sh
cd lanes/k8s
SIDES=direct ROUNDS=1 WORKLOADS=sweep-10,sweep-25,sweep-50,sweep-75,sweep-100 ./bench.sh all
```

The floor comes from a separate run, so it carries that run's noise: separate runs of the same
configuration drifted by about ±0.7 ms. The published floor is in the `floor_p50_ms` and
`floor_p99_ms` columns of [`results/better-benchmarks/sweep.csv`](../../results/better-benchmarks/sweep.csv).
