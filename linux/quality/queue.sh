#!/bin/bash
# queue.sh FILE: run the benchmark for each line of FILE, re-reading it after every run,
# so lines can be added while it works. Stops when nothing is left.
#   NAME ARGS...     wkbench.py run NAME ARGS...; done once runs/NAME/status.txt says ok
#                    (two attempts at most)
#   !NAME COMMAND    a shell command run once, in file order, e.g. installing another
#                    package and saving its settings with "wkbench.py baseline"
set -u
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
work="${AMDNR_BENCH_WORK:-$here/../build/nr-quality}"
queue="$(realpath -- "$1")"
mkdir -p "$work/runs"
declare -A tries
while true; do
  next=""; kind=""; nargs=""
  while read -r name args; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    if [[ "$name" == !* ]]; then
      [[ -e "$work/runs/.step-${name#!}" ]] && continue
      next="${name#!}"; nargs="$args"; kind=step; break
    fi
    grep -qs '^ok' "$work/runs/$name/status.txt" && continue
    (( ${tries[$name]:-0} >= 2 )) && continue
    next="$name"; nargs="$args"; kind=run; break
  done < "$queue"
  [[ -z "$next" ]] && break
  if [[ $kind == step ]]; then
    echo "step $next: $nargs"
    eval "$nargs" || echo "step $next failed"
    touch "$work/runs/.step-$next"
    continue
  fi
  tries[$next]=$(( ${tries[$next]:-0} + 1 ))
  eval "run_args=($nargs)"
  python3 "$here/wkbench.py" run "$next" "${run_args[@]}"
done
echo "queue empty"
