#!/bin/sh
# Run-owned, opt-in fixture. Default invocation only prints validated requests.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$here/lab_setup.py" ]; then
  exec python3 -B "$here/lab_setup.py" "$@"
fi
exec python3 -B "$here/../../src/offline_agent/uplink/lab_setup.py" "$@"
