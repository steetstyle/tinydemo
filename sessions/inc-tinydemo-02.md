# Live PRAXIS session — `inc-tinydemo-02`

A recorded, unedited run of the four tools driven by an AI agent (OpenCode, model
`mimo-v2.6-flash-free`) over MCP, on 2026-10-24. Nothing here is transcribed by
hand from a screen: the tool outputs below are copied from the session.

- **Input:** `data/failing-trace.json` + the `Agency.API` sources of the example solution.
- **Servers:** `tinypraxis serve` → spawns `tiny-rca serve` → `tinylink serve --index …`
  and `tiny-pdg-cs serve`.
- **Why this repo exists:** `demo.sh` proves the pipeline runs; this file proves an
  agent can complete the PRAXIS state machine end to end on that pipeline.

---

## Phase 1 — `praxis_begin`

```json
{
  "investigation": "inc-tinydemo-02",
  "phase": 1,
  "brief": {
    "failing_hops": [
      {
        "from": { "service": "Dashboard.API", "confidence": 0.7 },
        "to": {
          "service": "Agency.API",
          "method": "GetAgencyMembersEndpoint.Handler",
          "file": "…/Agency.API/Endpoints/Members/GetAgencyMembersEndpoint.cs",
          "line": 14,
          "confidence": 0.95
        },
        "endpoint": { "source": "GET /api/agency/{agencyId}/member",
                      "target": "GET /api/agency/{agencyId}/member" },
        "error_count": 1,
        "avg_latency_ms": 350,
        "trace_ids": ["a1b2c3d4e5f60718293a4b5c6d7e8f90"]
      }
    ],
    "services_in_the_graph": ["Agency.API", "Dashboard.API"],
    "incident": "1 failing hop(s) across 2 service(s). Dependency edges: Dashboard.API -> Agency.API. A failure propagates along these edges, so a service that only appears on the left of one has not yet been shown to fail…"
  },
  "lowest_mapping_confidence": 0.95
}
```

The trace-to-code mapping happened without being asked for: the failing hop came
back already pointing at `GetAgencyMembersEndpoint.Handler` / line 14.

## Phase 2 — `praxis_select`

`candidates: ["Agency.API", "Dashboard.API"]` → `added: 2`, queue `Agency.API, Dashboard.API`.

Selection is the agent's call, and it is the first place it can go wrong: the brief
ranks by failing hops, and the server says explicitly that the ranking is *evidence,
not a verdict*.

## Phase 3 — traversal

### Entity 1: `Agency.API`

Regions were not served by the server (the child `tiny-pdg-cs` connection inside
`tiny-rca` was not up during this session), so the agent obtained the same regions
directly from `tiny_pdg_cs.method_pdg`:

```json
{ "blocks": [ {"id":0,"kind":"Entry","start_line":14,"end_line":44},
              {"id":1,"kind":"Exit","start_line":14,"end_line":44},
              {"id":2,"kind":"Statement","start_line":23,"end_line":28},
              {"id":3,"kind":"Statement","start_line":30,"end_line":43} ],
  "edges": [ {"from":2,"kind":"cfg:Seq","to":3},
             {"from":0,"kind":"cfg:Seq","to":2},
             {"from":3,"kind":"cfg:Seq","to":1} ],
  "signature": "Task<IResult> Handler(Guid, GetAgencyMembersQueryHandler, int, int)" }
```

Four blocks, all edges `Seq` — the method has no branch at all.

| # | action | block | what the step showed |
|---|--------|-------|----------------------|
| 1 | `Relate` | block 2 (L23–28) → block 3 (L30–43) | block 2 is the only `await`: `handler.HandleAsync(…)`. Every exception the endpoint can surface enters there; block 3 only builds the 200 response. |
| 2 | `Discard` | block 3 (L30–43) | Dead end: no result object was ever produced (the store holds `InvalidOperationException: no name for agency 9f1c`). |
| 3 | `Expand` | block 2 (L23–28) → method region | No branch, no `try/catch`, no conditional in `Handler`. The throw originates below this method, inside `GetAgencyMembersQueryHandler.HandleAsync`. |
| 4 | `Complete` | method region L14–44 | Traversal finished; the remaining question sits outside this entity's own regions. |

```json
{ "entity": "Agency.API", "role": "Primary Failure", "progress": { "judged": 1, "primary_failures": 1 } }
```

### Entity 2: `Dashboard.API`

No regions available; the server itself said *complete in one step and judge on what
the observability says*. `failing_hops: 0` — nothing failed inside it.

| # | action | block | what the step showed |
|---|--------|-------|----------------------|
| 1 | `Complete` | service region | 0 failing hops; span carries 500 for `GET /api/dashboard`; it sits left of the only edge, i.e. it is the caller. |

```json
{ "entity": "Dashboard.API", "role": "Symptom Only" }
```

`praxis_update_queue` then returned `queue: "empty"` — the gate that lets
`praxis_report` run.

## Phase 4 — `praxis_report`

```json
{
  "phase": 4,
  "coverage": { "judged_every_queued_entity": true, "queue_drained": true, "left_unjudged": "" },
  "primary_failures": ["Agency.API"],
  "symptom_only": ["Dashboard.API"],
  "graph": { "services": ["Agency.API", "Dashboard.API"],
             "hops": [{"from": "Dashboard.API", "to": "Agency.API", "confidence": 0.95}] },
  "progress": { "judged": 2, "primary_failures": 1, "symptom_only": 1, "unrelated": 0, "queued": 0 },
  "what_this_report_is_not": "PRAXIS constrains the search; it does not verify the verdict. … Say which claim would test it."
}
```

**Verdict:** `Agency.API` = Primary Failure, `Dashboard.API` = Symptom Only, after
5 steps and 2 judgements, with 16 of 20 blocks left in the per-entity budget
(`entities_remaining_budget: 14` of 16 entities).

---

## Observations worth carrying into the thesis

1. **The budget never bound.** The whole incident was settled in 5 steps against a
   20-block budget. On a two-service graph the constraint is inert; its value has to
   be shown on a graph where an unconstrained agent would wander — which is exactly
   the ablation the PRAXIS paper runs (Eff. ATC 884.9k → 166.5k). Our contribution
   claim must be about *isolating the constraint*, not about beating this number.
2. **`Discard` then `Expand` was accepted.** Step 2 returned `traversal_closed: true`
   after `Discard`, and step 3 (`Expand`) was still accepted against the same entity
   with `blocks_remaining` decremented normally. Whether the state machine should
   reopen after a discard is a question for the implementer, not a defect we can
   assert from one run. Recorded here as an observation to re-test, not as a bug.
3. **Regions came from outside the server.** `praxis_next` reported
   `regions_available: false` because `tiny-rca`'s child `tiny-pdg-cs` was not
   running, and told the agent to `Complete` immediately. The agent instead fetched
   the PDG directly and used it. That is a legitimate path — but note the server
   accepted `block` names it had not issued itself. On the thesis side: the
   traversal record is agent-authored text, so *what counts as a region* needs to be
   pinned down before any accuracy number means anything.
4. **`logs_considered: 0` while the agent quoted log lines.** The report's evidence
   section counted zero logs, because the log store the agent searched (ingested via
   `tinylink logs-ingest` in `demo.sh` stage 04) is not the one the report reads
   (`log_source_note`: *"the backend did not report how many records its store
   holds"*). Two stores, one incident. Fix by naming a single store —
   `TINYLINK_LOG_STORE` — for both the ingest and the server.
