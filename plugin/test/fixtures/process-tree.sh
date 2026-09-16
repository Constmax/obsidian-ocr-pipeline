#!/bin/bash
# Stands in for reprocess-raw in the process-tree test of conversion.test.ts.
# $1 is a file that receives the PID of every descendant, one per line. One
# subtree ignores SIGTERM, like a worker that keeps running after the script
# itself stopped, so cancellation has to escalate to SIGKILL.

pids="$1"

sleep 60 &
echo $! >> "$pids"

(
    trap '' TERM
    sleep 60 &
    echo $! >> "$pids"
    wait
) &
echo $! >> "$pids"

(
    sleep 60 &
    echo $! >> "$pids"
    wait
) &
echo $! >> "$pids"

wait
