#!/bin/bash
set -euo pipefail
DIR="$HOME/Library/Application Support/flclash-probe"
exec "$DIR/venv/bin/python3" "$DIR/probe.py" "$@"
