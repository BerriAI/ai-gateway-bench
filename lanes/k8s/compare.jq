# One markdown row of the comparison for one loadgen result file (slurped, -s).
# Args: $win  {workload, side, round, start, end} (epoch seconds of the run)
#       $valid "yes" or why the run is invalid
#       $maxlag client lag p99 (ms) above which the run is invalid
#       $res   metrics.k8s.io samples [{t, side, cpu: "123456n", mem: "123456Ki"}]
# Overheads follow kit/cmd/summarize: first byte against the planned time to
# first token (streams) or the planned duration (non-streaming), total against the planned duration.

def pct(p): if length == 0 then null else sort | .[((p / 100) * (length - 1)) | round] end;
def ms: if . == null then "-" else (. * 10 | round / 10 | tostring) end;
def cores: if endswith("n") then (.[:-1] | tonumber) / 1e9
  elif endswith("u") then (.[:-1] | tonumber) / 1e6
  elif endswith("m") then (.[:-1] | tonumber) / 1e3
  else tonumber end;
def mib: if endswith("Ki") then (.[:-2] | tonumber) / 1024
  elif endswith("Mi") then (.[:-2] | tonumber)
  elif endswith("Gi") then (.[:-2] | tonumber) * 1024
  else (tonumber / 1048576) end;
def r1: . * 10 | round / 10;

map(select(.warmup | not)) as $all
| ($all | map(select(.ok))) as $ok
# Kits older than lag_ms have none, and the column shows "-".
| ([$all[] | .lag_ms | select(. != null)] | pct(99)) as $lag
| (if ($all | length) > 1 then (($all | map(.t) | max) - ($all | map(.t) | min)) / 1000 else 0 end) as $span
# One sample per scrape: a side with several replicas is the sum over them.
| ($res | map(select(.side == $win.side and .t >= $win.start and .t <= $win.end and .cpu != null))
    | group_by(.t) | map({cpu: (map(.cpu | cores) | add), mem: (map(.mem | mib) | add)})) as $samples
| [
    $win.workload, $win.side, ($win.round | tostring),
    ($all | length | tostring),
    (if ($all | length) > 0 then (100 * ($ok | length) / ($all | length) | r1 | tostring) else "-" end),
    (if $span > 0 then (($ok | map(.ctx) | add // 0) / $span | round | tostring) else "-" end),
    (if $span > 0 then (($ok | map(.out) | add // 0) / $span | round | tostring) else "-" end),
    ([$ok[] | .ttfb_ms - (if .stream then .ttft_plan_ms else .dur_plan_ms end)] as $x
      | "\($x | pct(50) | ms) / \($x | pct(99) | ms)"),
    ([$ok[] | .total_ms - .dur_plan_ms] as $x | "\($x | pct(50) | ms) / \($x | pct(90) | ms) / \($x | pct(99) | ms)"),
    ([$ok[] | select(.stream) | .max_gap_ms // 0] | pct(99) | ms),
    ($lag | ms),
    (if ($samples | length) > 0 then ($samples | map(.cpu) | add / length | . * 100 | round / 100 | tostring) else "-" end),
    (if ($samples | length) > 0 then ($samples | map(.mem) | max | round | tostring) else "-" end),
    (if $valid == "yes" and $lag != null and $lag > $maxlag then "no (client lag p99 \($lag | ms) ms)" else $valid end)
  ]
| "| " + join(" | ") + " |"
