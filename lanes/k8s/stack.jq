# One markdown row of the stack-load table for one run: mean CPU cores and peak memory of
# the load generator, the mock, s3stub and Postgres while that run's window was open.
# Args: $win {workload, side, round, start, end}; $res the resource samples ({t, role, cpu, mem}).

def cores: if endswith("n") then (.[:-1] | tonumber) / 1e9
  elif endswith("u") then (.[:-1] | tonumber) / 1e6
  elif endswith("m") then (.[:-1] | tonumber) / 1e3
  else tonumber end;
def mib: if endswith("Ki") then (.[:-2] | tonumber) / 1024
  elif endswith("Mi") then (.[:-2] | tonumber)
  elif endswith("Gi") then (.[:-2] | tonumber) * 1024
  else (tonumber / 1048576) end;

def load($role):
  [$res[] | select(.role == $role and .t >= $win.start and .t <= $win.end and .cpu != null)]
  | if length == 0 then "-"
    else "\(map(.cpu | cores) | add / length | . * 100 | round / 100) / \(map(.mem | mib) | max | round)" end;

[$win.workload, $win.side, ($win.round | tostring),
  load("client"), load("mockllm"), load("s3stub"), load("postgresql")]
| "| " + join(" | ") + " |"
