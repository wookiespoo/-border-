#!/bin/bash
# Run a local 3-node Border testnet without Docker.
# Each node runs in the background on a different port.
#
# Usage: bash testnet/run_local_testnet.sh
# Stop:  bash testnet/run_local_testnet.sh stop

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PIDS_FILE="$SCRIPT_DIR/.testnet_pids"

export BORDER_NETWORK=testnet
export PYTHONPATH="$REPO_ROOT"

start() {
  echo "Starting Border local testnet (3 nodes)..."
  mkdir -p /tmp/border-testnet/{node1,node2,node3}

  python -m border.node_runner \
    --host 127.0.0.1 --port 9001 \
    --data-dir /tmp/border-testnet/node1 \
    --storage --compute --dns &
  echo $! >> "$PIDS_FILE"
  echo "  Node 1 started (port 9001, seed)"

  sleep 1

  python -m border.node_runner \
    --host 127.0.0.1 --port 9002 \
    --data-dir /tmp/border-testnet/node2 \
    --peers 127.0.0.1:9001 \
    --storage --compute &
  echo $! >> "$PIDS_FILE"
  echo "  Node 2 started (port 9002)"

  python -m border.node_runner \
    --host 127.0.0.1 --port 9003 \
    --data-dir /tmp/border-testnet/node3 \
    --peers 127.0.0.1:9001 &
  echo $! >> "$PIDS_FILE"
  echo "  Node 3 started (port 9003)"

  echo ""
  echo "Testnet running. Check status:"
  echo "  curl http://127.0.0.1:9001/status"
  echo "  curl http://127.0.0.1:9002/status"
  echo "  curl http://127.0.0.1:9003/status"
  echo ""
  echo "Stop with: bash testnet/run_local_testnet.sh stop"
}

stop() {
  if [ ! -f "$PIDS_FILE" ]; then
    echo "No testnet running."
    return
  fi
  echo "Stopping Border testnet..."
  while read -r pid; do
    kill "$pid" 2>/dev/null && echo "  Stopped PID $pid" || true
  done < "$PIDS_FILE"
  rm -f "$PIDS_FILE"
  echo "Done."
}

case "${1:-start}" in
  start) start ;;
  stop)  stop  ;;
  *)     echo "Usage: $0 [start|stop]" ;;
esac
