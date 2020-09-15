#!/usr/bin/env bash
set -e
set -x

BLACKLIST="^(2to3|pickle|unpickle|python_startup|regex_compile|tornado)"

test "$OSTYPE" = "linux-gnu" && TRACK_MEMORY=--track-memory

rm -f pyddprofile-memalloc.pyperformance.json pyddprofile.pyperformance.json python.pyperformance.json

export DD_PROFILING_API_TIMEOUT=0.1

python -m pyperformance list --inside-venv | awk '/^-/{print $2}' | grep -v -E "$BLACKLIST" | while read benchmark
do
    echo "=========== $benchmark ==========="
    # We need to run benchmark in worker mode to have a process running long
    # enough to gather the overhead induced by the profiling. When run in
    # default mode, `perf` can calibrate loops but also runs a subprocess for
    # each set of loops. That does not allow us to measure correctly the
    # profiling overhead. The downside is that we have no clue how long a loop
    # is; sometimes it's fast, sometimes it's not. Some tests can run for
    # several minutes.
    python -m pyperformance.benchmarks.bm_"$benchmark" --worker --loops=20 --warmups=0 --append python.pyperformance.json --stat $TRACK_MEMORY
    pyddprofile -m pyperformance.benchmarks.bm_"$benchmark" --worker --loops=20 --warmups=0 --append pyddprofile.pyperformance.json --stat $TRACK_MEMORY
    _DD_PROFILING_MEMALLOC=1 pyddprofile -m pyperformance.benchmarks.bm_"$benchmark" --worker --loops=20 --warmups=0 --append pyddprofile-memalloc.pyperformance.json --stat $TRACK_MEMORY
done

python -m pyperf compare_to -v python.pyperformance.json pyddprofile.pyperformance.json pyddprofile-memalloc.pyperformance.json
