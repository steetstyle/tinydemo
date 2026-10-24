#!/usr/bin/env bash
# Reproducible live run: one complete PRAXIS investigation, no LLM in the loop.
#
#   ./live.sh
#
# Contrasts with ./demo.sh (does the tools work?) and with
# sessions/inc-tinydemo-02.md (can an agent reason through the state machine?).
# This one answers: can the whole cycle be re-run by anyone, on any machine, and
# produce the same verdicts? Everything it needs is passed explicitly — no PATH,
# no ambient config, no shared log store by accident.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SIB="$(cd "$HERE/.." && pwd)"

TINYLINK_BIN="${TINYLINK_BIN:-$SIB/tinylink/target/release/tinylink}"
PDG_BIN="${PDG_BIN:-$SIB/tiny-pdg-cs/target/release/tiny-pdg-cs}"
RCA_BIN="${RCA_BIN:-$SIB/tiny-rca/target/release/tiny-rca}"
PRAXIS_BIN="${PRAXIS_BIN:-$SIB/tinypraxis/target/release/tinypraxis}"
SOLUTION="${EXAMPLE_SOLUTION:-$SIB/tinylink/example-projects/unicpeak-analytics-api}"

for pair in "tinylink:$TINYLINK_BIN" "tiny-pdg-cs:$PDG_BIN" "tiny-rca:$RCA_BIN" \
            "tinypraxis:$PRAXIS_BIN"; do
    [[ -x "${pair#*:}" ]] || { echo "✗ ${pair%%:*} not built: ${pair#*:}" >&2; exit 1; }
done
[[ -d "$SOLUTION" ]] || { echo "✗ example solution missing: $SOLUTION" >&2; \
    echo "  run: bash $SIB/tinylink/example-projects/fetch.sh" >&2; exit 1; }

# stage 04 builds the index and the log store this run reads
[[ -f "$HERE/out/tinylink.db" ]] || "$HERE/demo.sh" 04 >/dev/null

exec python3 "$HERE/scripts/praxis_client.py" \
    --praxis    "$PRAXIS_BIN" \
    --rca       "$RCA_BIN" \
    --tinylink  "$TINYLINK_BIN" \
    --pdg       "$PDG_BIN" \
    --index     "$HERE/out/tinylink.db" \
    --sources   "$SOLUTION/Agency.API" \
    --trace     "$HERE/data/failing-trace.json" \
    --log-store "$HERE/out/tinylink.logs.db" \
    --outdir    "$HERE/out"
