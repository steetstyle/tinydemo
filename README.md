# tinydemo

One command that runs the four `tiny*` tools end to end, plus a recorded live agent
session over their MCP endpoints.

```
trace + logs ──► tinylink ──► tiny-rca ──► tinypraxis
   OTLP JSON       SDG + code     MCP          PRAXIS traversal
                   mapping        orchestr.    {Expand, Relate, Complete, Discard}
        ▲                                            ▲
        │                                            │
   tiny-pdg-cs ──── C# route / call graph / PDG ─────┘
```

| repo | layer | what it contributes here |
|------|-------|--------------------------|
| [tiny-pdg-cs](../tiny-pdg-cs) | static code graph | routes, call graph, PDG blocks of the failing method |
| [tinylink](../tinylink) | trace ↔ code | SDG from the trace, the errored hop mapped to `file:line`, SQLite index, log store |
| [tiny-rca](../tiny-rca) | orchestration | one MCP server over tinylink + tiny-pdg-cs + telemetry |
| [tinypraxis](../tinypraxis) | search policy | PRAXIS state machine: queue, budget, roles, report |

## Quick start

```bash
# 1. build the tools (once)
cd ../tinylink    && cargo build --release
cd ../tiny-pdg-cs && cargo build --release --features mcp   # plain build = CLI only
cd ../tiny-rca    && cargo build --release
cd ../tinypraxis  && cargo build --release

# 2. get the analysed solution (public clone, ~1667 C# files)
bash ../tinylink/example-projects/fetch.sh

# 3. run
./demo.sh            # or: ./demo.sh 01 03   for specific stages
```

Everything lands in `out/` (git-ignored). Nothing outside `out/` is written.

## What each stage does — and what it printed

### Stage 01 — code graph (`tiny-pdg-cs`)

```
routes:        out/01-routes.txt (14 routes)
call graph:    out/01-callgraph.txt
handler PDG:   out/01-handler-pdg.dot
```

```
  GetAgencyMembersEndpoint (1 route):
       GET  /api/agency/{agencyId}/member   Handler
```

### Stage 02 — service dependency graph (`tinylink sdg`)

`out/02-sdg.dot` renders the graph the trace implies:

```dot
Agency.API [label="Agency.API\nerrors: 1" color=red penwidth=2];
Dashboard.API [label="Dashboard.API\nerrors: 1" color=red penwidth=2];
Dashboard.API -> Agency.API [color=red penwidth=2
  label="GET /api/agency/{agencyId}/member\ncalls:1 ERRORS:1 avg:350ms P50:350 P95:350 P99:350"];
```

### Stage 03 — trace → code (`tinylink query / tracepath / link`)

The point of the whole pipeline: a failing hop becomes a file and a line.

```
Error Paths (1 edge(s)):
#      From                 To                   Method                         File:Line
------------------------------------------------------------------------------------------
  1    Dashboard.API      → Agency.API         GetAgencyMembersEndpoint.Handler
       …/Agency.API/Endpoints/Members/GetAgencyMembersEndpoint.cs:14
```

```
Trace Path (1 steps):
  1    Dashboard.API   → Agency.API   GetAgencyMembersEndpoint.Handler ⚠  350 ms
```

### Stage 04 — index + logs (`tinylink index / logs-ingest / logs-search`)

```
Indexed 81 methods, 67 routes, 407 call edges across 2 project(s)
Wrote out/tinylink.db (188.0 KiB), content hash 1a7897df2aa4
```

```
[Error] Agency.API — Handler {HandlerName} failed: {Message}
    HandlerName = GetAgencyMembers
    Message = no name for agency 9f1c
    exception.message = no name for agency 9f1c
```

The log side and the trace side describe the same incident — that is what makes a
joint trace+log RCA possible at all.

### Stage 05 — MCP endpoints (stdio smoke test)

Each server is started, sent `initialize` + `tools/list`, and shut down. Real
numbers, written to `out/05-mcp.txt`:

```
tinylink   blast_radius, explain_error, find_methods, ingest_logs, list_indexed_services, search_logs
tiny-rca   capabilities, diff_impact, explain_method, ingest_logs, investigate_error, method_dependencies, …
tinypraxis praxis_begin, praxis_capabilities, praxis_diff, praxis_explain, praxis_judge, praxis_next, …
```

## The live part: an agent driving PRAXIS

`demo.sh` stops at "the servers speak MCP". The actual point is an agent *using*
them. [`sessions/inc-tinydemo-02.md`](sessions/inc-tinydemo-02.md) is a recorded,
unedited run of one complete investigation:

```
praxis_begin → praxis_select → praxis_next → step ×4 → judge → update_queue
             → praxis_next → step → judge → update_queue → praxis_report
```

result:

```json
{ "primary_failures": ["Agency.API"], "symptom_only": ["Dashboard.API"],
  "coverage": { "judged_every_queued_entity": true, "queue_drained": true } }
```

To reproduce it yourself, wire the three servers into an agent client with
[`agent/opencode.json`](agent/opencode.json):

```bash
cp agent/opencode.json <your-project>/.opencode.json   # adjust paths
opencode   # then: "use tinypraxis to investigate data/failing-trace.json"
```

## Reproducing the run: `./live.sh`

The agent run above cannot be replayed — the reasoning happened once, in a model.
`live.sh` is the answer: **the same cycle, no LLM, byte-reproducible.**

```bash
./live.sh
# verdicts: Agency.API = Primary Failure, Dashboard.API = Symptom Only
# coverage: {"judged_every_queued_entity": true, "left_unjudged": "", "queue_drained": true}
# transcript: out/live-session.json (33 messages)
```

It spawns `tinypraxis serve` and passes **every** backend explicitly
(`--rca`, `--tinylink`, `--index`, `--pdg`, `--sources`, `TINYLINK_LOG_STORE`), so
nothing depends on `PATH` or on an ambient config. It then runs `praxis_begin →
select → next → step* → judge → update_queue` until the server itself answers
`Q is empty` — which is treated as the termination signal — and writes the full
JSON-RPC transcript to `out/live-session.json`.

**What the scripted policy is.** Every step is a fixed rule declared at the top of
[`scripts/praxis_client.py`](scripts/praxis_client.py):

| | rule |
|---|---|
| step policy | ≥2 statement blocks: `Relate b0→b1`, `Discard b1`, `Expand b0`, `Complete` · 1 block: `Expand`, `Complete` · 0 blocks: `Complete` |
| verdict rule | `failing_hops > 0` → **Primary Failure**, else **Symptom Only** |

It reached the same verdicts as the agent run, and it did so on the **real PDG
regions** (`block 2 (L23-28)` → `block 3 (L30-43)`) — this run passes `--pdg`, so
`tiny-rca`'s child `tiny-pdg-cs` is up and `regions_available` is true.

Be precise about what this shows: **the state machine and its gates are
reproducible**; the *judgement* here is a one-line rule, not reasoning. The two
runs side by side are exactly the thesis's baseline-vs-constrained comparison —
`sessions/` holds the reasoning run, `live.sh` holds the mechanical one.

```
./demo.sh     # do the tools work?            → out/01…05
./live.sh     # does the cycle replay?        → out/live-session.json
sessions/     # can an agent reason through it? → inc-tinydemo-02.md
```

## Data

| file | origin |
|------|--------|
| `data/failing-trace.json` | synthetic OTLP/JSON, generated by `data/gen_trace.py`; route + handler are real |
| `data/otlp-logs-capture.bin` | byte-identical copy of `tinylink/tests/fixtures/otlp-logs-capture.bin`, sha256 `c31b89cf…acd10ac1` |

Full provenance, including the parser quirk (`http.status_code` must be a JSON
*number*), in [`data/PROVENANCE.md`](data/PROVENANCE.md).

## What this demo does *not* claim

- **No accuracy claim.** The failing route and the analysed source come from the
  same solution — the *inverse crime* setting. The demo shows the wiring, not a
  measured error rate. Any number the thesis reports has to come from the
  experiment in `tez-onerisi`, not from here.
- **No "we beat PRAXIS" claim.** Different language, different data, different model.
  What can be reproduced here is the *behaviour*: how a traversal ends, what the
  queue admits, what the budget costs.
- **The log store is split.** `demo.sh` writes `out/tinylink.logs.db` while a server
  started elsewhere may read another path; `TINYLINK_LOG_STORE` must name one place
  for both, otherwise `logs_considered: 0` while the agent still finds records.
  Documented as observation 4 in the session file.

---

## Türkçe özet

Bu depo, dört aracın **tek komutla uçtan uca** çalıştığını kanıtlayan canlı bir
demodur: `./demo.sh` çalıştırıldığında C# kodundan rota/PDG grafiği (tiny-pdg-cs),
izden servis grafiği ve `dosya:satır` eşlemesi (tinylink), kalıcı indeks + log
arama, ve nihayet üç MCP sunucusunun ayağa kalktığı bir ajan denemesi çıkar
(`out/`).

`./live.sh` ise aynı PRAXIS döngüsünü **model olmadan, birebir tekrarlanabilir**
olarak koşar: `begin → select → next → step → judge → update_queue` sunucunun
`Q is empty` yanıtına kadar, adım adım gerçek PDG blokları üzerinde; transkript
`out/live-session.json` dosyasına yazılır. Kararlar ajan koşusuyla aynıdır
(`Agency.API = Primary Failure`, `Dashboard.API = Symptom Only`) — fark,
akıl yürütmenin bir satırlık kural olmasıdır; tezde **mekanik taban** ile
**ajan koşusu** arasındaki karşılaştırma tam buradan kurulur.

`sessions/inc-tinydemo-02.md` dosyası, bir ajanın PRAXIS durum makinesini
`begin → select → step → judge → queue → report` şeklinde **hatasız** tamamladığı
canlı oturumun kaydıdır — tezin "yöntem" ve "bulgular" bölümleri için hazır bir
çalışma örneğidir. `data/PROVENANCE.md` verinin nereden geldiğini, son bölüm ise
bu demodan **çıkarılamayacak** iddiaları (inverse crime, doğruluk iddiası yok)
listeler.
