#!/usr/bin/env bash
# tinydemo — one-command end-to-end run of the four tiny* tools.
#
#   stage 01  code graph        tiny-pdg-cs   route / callgraph / pdg
#   stage 02  service graph     tinylink      sdg   (json + dot)
#   stage 03  trace -> code     tinylink      query / tracepath / link
#   stage 04  index + logs      tinylink      index / logs-ingest / logs-search
#   stage 05  MCP endpoints     tinylink, tiny-rca, tinypraxis  (stdio smoke test)
#
# Artifacts are written to out/. Nothing outside out/ is touched.
#
# Usage:
#   ./demo.sh                 # all stages
#   ./demo.sh 02 03           # only stages 02 and 03

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SIB="$(cd "$HERE/.." && pwd)"

# ---- where the four binaries live -------------------------------------
TINYLINK_BIN="${TINYLINK_BIN:-$SIB/tinylink/target/release/tinylink}"
PDG_BIN="${PDG_BIN:-$SIB/tiny-pdg-cs/target/release/tiny-pdg-cs}"
RCA_BIN="${RCA_BIN:-$SIB/tiny-rca/target/release/tiny-rca}"
PRAXIS_BIN="${PRAXIS_BIN:-$SIB/tinypraxis/target/release/tinypraxis}"

# ---- what we analyse ---------------------------------------------------
# A real ASP.NET Core solution (public clone). Override with EXAMPLE_SOLUTION.
SOLUTION="${EXAMPLE_SOLUTION:-$SIB/tinylink/example-projects/unicpeak-analytics-api}"
TRACE="$HERE/data/failing-trace.json"
LOGS="$HERE/data/otlp-logs-capture.bin"

OUT="$HERE/out"
mkdir -p "$OUT"

need() {
    local bin="$1" name="$2"
    if [[ ! -x "$bin" ]]; then
        echo "✗ $name not found at $bin" >&2
        echo "  build it first, e.g. cd $SIB/tiny-pdg-cs && cargo build --release" >&2
        exit 1
    fi
}

banner() { printf '\n\033[1m── %s ─────────────────────────────────────────────\033[0m\n' "$*"; }
info()   { printf '   %s\n' "$*"; }

stage01() {
    banner "01  code graph  (tiny-pdg-cs)"
    "$PDG_BIN" route "$SOLUTION/Agency.API"  >"$OUT/01-routes.txt"
    info "routes:        out/01-routes.txt ($(grep -cE '^\s+(GET|POST|PUT|DELETE|PATCH)' "$OUT/01-routes.txt") routes)"
    "$PDG_BIN" callgraph "$SOLUTION/Agency.API" --class GetAgencyMembersEndpoint --inbound \
                 >"$OUT/01-callgraph.txt"
    info "call graph:    out/01-callgraph.txt"
    "$PDG_BIN" pdg "$SOLUTION/Agency.API/Endpoints/Members/GetAgencyMembersEndpoint.cs" \
                 --format dot >"$OUT/01-handler-pdg.dot"
    info "handler PDG:   out/01-handler-pdg.dot"
}

stage02() {
    banner "02  service dependency graph  (tinylink sdg)"
    "$TINYLINK_BIN" sdg "$TRACE" --stats >"$OUT/02-sdg.json"
    info "sdg json:      out/02-sdg.json"
    "$TINYLINK_BIN" sdg "$TRACE" --format dot >"$OUT/02-sdg.dot"
    info "sdg dot:       out/02-sdg.dot  (render: dot -Tpng out/02-sdg.dot)"
}

stage03() {
    banner "03  trace -> code  (tinylink query / tracepath / link)"
    "$TINYLINK_BIN" query "$TRACE" "$SOLUTION/Agency.API" >"$OUT/03-error-paths.txt" 2>&1
    info "error paths:   out/03-error-paths.txt"
    "$TINYLINK_BIN" tracepath "$TRACE" "$SOLUTION/Agency.API" >"$OUT/03-tracepath.txt" 2>&1
    info "trace path:    out/03-tracepath.txt"
    "$TINYLINK_BIN" link "$TRACE" "$SOLUTION/Agency.API" --format dot >"$OUT/03-linked.dot" 2>&1
    info "linked graph:  out/03-linked.dot"
}

stage04() {
    banner "04  index + logs  (tinylink index / logs-ingest / logs-search)"
    "$TINYLINK_BIN" index "$SOLUTION/Agency.API,$SOLUTION/Dashboard.API" \
                 -o "$OUT/tinylink.db" --force >"$OUT/04-index.txt" 2>&1
    info "index:         out/tinylink.db — $(head -1 "$OUT/04-index.txt")"
    ( cd "$OUT" && TINYLINK_LOG_STORE="$OUT/tinylink.logs.db" \
        "$TINYLINK_BIN" logs-ingest "$LOGS" >"$OUT/04-logs-ingest.txt" 2>&1 )
    info "log store:     out/tinylink.logs.db"
    ( cd "$OUT" && TINYLINK_LOG_STORE="$OUT/tinylink.logs.db" \
        "$TINYLINK_BIN" logs-search --min-severity Error --limit 3 >"$OUT/04-logs-search.txt" 2>&1 )
    info "error logs:    out/04-logs-search.txt"
}

stage05() {
    banner "05  MCP endpoints  (stdio smoke test)"
    local init='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"tinydemo","version":"0"}}}'
    local list='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
    local report="$OUT/05-mcp.txt"
    : >"$report"
    for pair in "tinylink:$TINYLINK_BIN" "tiny-rca:$RCA_BIN" "tinypraxis:$PRAXIS_BIN"; do
        local name="${pair%%:*}" bin="${pair#*:}"
        local args=()
        [[ "$name" == "tinylink" ]] && args=(serve --index "$OUT/tinylink.db")
        [[ "$name" != "tinylink" ]] && args=(serve)
        local resp tools
        resp=$(printf '%s\n%s\n' "$init" "$list" | TINYLINK_LOG_STORE="$OUT/tinylink.logs.db" \
               timeout 5 "$bin" "${args[@]}" 2>/dev/null | grep '"id":2' | head -1)
        tools=$(python3 -c '
import json,sys
try:
    d=json.loads(sys.argv[1])
    print(", ".join(t["name"] for t in d["result"]["tools"]))
except Exception:
    print("(no tools/list response)")
' "$resp")
        printf '%-10s %s\n' "$name" "$tools" >>"$report"
        if [[ "$tools" != "(no tools/list response)" ]]; then
            info "$name  ✓ $(cut -c1-96 <<<"$tools")…"
        else
            info "$name  ✗ no response"
        fi
    done
    info "tool lists:    out/05-mcp.txt"
    info "wiring:        see agent/opencode.json"
}

# ---- main --------------------------------------------------------------
if [[ ! -f "$TRACE" ]]; then echo "✗ missing $TRACE" >&2; exit 1; fi
if [[ ! -d "$SOLUTION" ]]; then
    echo "✗ example solution not found at $SOLUTION" >&2
    echo "  run: bash $SIB/tinylink/example-projects/fetch.sh   (public clone, ~1667 C# files)" >&2
    exit 1
fi
need "$TINYLINK_BIN"   tinylink
need "$PDG_BIN"        tiny-pdg-cs
need "$RCA_BIN"        tiny-rca
need "$PRAXIS_BIN"     tinypraxis

stages=("$@")
[[ ${#stages[@]} -eq 0 ]] && stages=(01 02 03 04 05)

for s in "${stages[@]}"; do "stage$s"; done

banner "done — artifacts in $OUT"
ls -la "$OUT" | tail -n +4
