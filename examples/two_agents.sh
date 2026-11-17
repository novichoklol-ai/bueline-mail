#!/usr/bin/env bash
# Two agents exchanging a task over BueLine Mail — minimal demo.
# Start a local server first:
#   export MAILRELAY_TOKEN_ALICE=$(openssl rand -hex 24)
#   export MAILRELAY_TOKEN_BOB=$(openssl rand -hex 24)
#   python3 bueline_mail.py --port 4224 &
set -euo pipefail
cd "$(dirname "$0")/.."

export BUELINE_URL=http://127.0.0.1:4224

echo "--- alice sends a task to bob"
BUELINE_TOKEN="$MAILRELAY_TOKEN_ALICE" python3 client/bueline.py \
    send --to bob --subject "task" --body "summarize report.txt" --ref "demo-1"

echo "--- bob picks it up"
BUELINE_TOKEN="$MAILRELAY_TOKEN_BOB" python3 client/bueline.py inbox
BUELINE_TOKEN="$MAILRELAY_TOKEN_BOB" python3 client/bueline.py ack --ids 1 --state in_work

echo "--- bob finishes and closes the task"
BUELINE_TOKEN="$MAILRELAY_TOKEN_BOB" python3 client/bueline.py ack --ids 1 --state done

echo "--- bob's box status (nothing stale)"
BUELINE_TOKEN="$MAILRELAY_TOKEN_BOB" python3 client/bueline.py status
