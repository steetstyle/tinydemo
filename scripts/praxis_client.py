#!/usr/bin/env python3
"""Drive one complete PRAXIS investigation over stdio MCP and save the transcript.

This is a *scripted policy*, not an LLM: every step, insight and verdict below is a
deterministic rule fixed in this file, so the run is reproducible byte for byte.
The LLM-driven counterpart — where the reasoning is genuinely the agent's — is
recorded in sessions/inc-tinydemo-02.md.

Rules encoded here (they are deliberately simple, and they are stated in the
thesis as a *baseline policy*, not as intelligence):

  step policy    ≥2 statement blocks : Relate b0->b1, Discard b1, Expand b0, Complete
                 1 statement block   : Expand b0, Complete
                 0 blocks            : Complete the entity region
  verdict rule   failing_hops > 0  -> Primary Failure
                 failing_hops == 0 -> Symptom Only

Usage: see live.sh, which resolves every path.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time


class Client:
    """Minimal newline-delimited JSON-RPC client for an MCP stdio server."""

    def __init__(self, argv, env):
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        self.id = 0
        self.transcript = []

    def _write(self, msg):
        self.transcript.append({"dir": ">>", **msg})
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def notify(self, method, params=None):
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(self, method, params=None, timeout=90):
        self.id += 1
        want = self.id
        self._write({"jsonrpc": "2.0", "id": want, "method": method,
                     "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"{method}: server closed stdout")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.transcript.append({"dir": "<<", **msg})
            if msg.get("id") == want:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(f"{method}: no response in {timeout}s")

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def tool(client, name, arguments):
    """Call an MCP tool and fail loudly if the server reported isError."""
    res = client.call("tools/call", {"name": name, "arguments": arguments})
    if res.get("isError"):
        text = res.get("content", [{}])[0].get("text", "?")
        raise RuntimeError(f"{name} failed: {text}")
    return json.loads(res["content"][0]["text"])


def find_blocks(obj, out=None):
    """Recursively collect every {id, kind, start_line} dict the server returned."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        if {"id", "kind", "start_line"} <= set(obj):
            out.append(obj)
        for v in obj.values():
            find_blocks(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_blocks(v, out)
    return out


def step_plan(blocks):
    """Deterministic action plan for one entity. See module docstring."""
    stmts = sorted([b for b in blocks if b.get("kind") == "Statement"],
                   key=lambda b: b["start_line"])
    label = lambda b: f"block {b['id']} (L{b['start_line']}-{b['end_line']})"
    plan = []
    if len(stmts) >= 2:
        a, b = stmts[0], stmts[1]
        plan += [
            ("Relate", label(a), label(b),
             f"{label(a)} covers L{a['start_line']}-{a['end_line']} and {label(b)} covers "
             f"L{b['start_line']}-{b['end_line']}: stepping from the first to the second "
             "to see whether the symptom reaches it."),
            ("Discard", label(b), None,
             f"{label(b)} does not carry the symptom for this incident, so it is dropped as "
             "a dead end; a discarded region is not evidence about the entity."),
            ("Expand", label(a), None,
             f"Widening from {label(a)} to the enclosing region: the enclosing method has "
             "no branch of its own that could raise, so the origin lies below it."),
        ]
    elif len(stmts) == 1:
        a = stmts[0]
        plan.append(("Expand", label(a), None,
                     f"{label(a)} is the only statement region (L{a['start_line']}-{a['end_line']}); "
                     "widening to the enclosing region to close the traversal at method scope."))
    plan.append(("Complete", "entity region", None,
                 "Every available region has been visited; ending the traversal for this entity."))
    return plan


def main():
    ap = argparse.ArgumentParser()
    for name in ("praxis", "rca", "tinylink", "pdg", "index", "sources", "trace",
                 "log_store", "outdir"):
        ap.add_argument(f"--{name.replace('_', '-')}", required=True)
    ap.add_argument("--investigation", default="inc-live-01")
    args = ap.parse_args()

    env = dict(os.environ)
    env["TINYLINK_LOG_STORE"] = args.log_store

    # tinypraxis spawns tiny-rca, which in turn spawns tinylink and tiny-pdg-cs.
    # Every one of them is named here: nothing in this run depends on a global PATH.
    argv = [
        args.praxis, "serve",
        f"--rca={args.rca}",
        f"--rca-arg=--tinylink", f"--rca-arg={args.tinylink}",
        f"--rca-arg=--index",    f"--rca-arg={args.index}",
        f"--rca-arg=--pdg",      f"--rca-arg={args.pdg}",
        f"--rca-arg=--sources",  f"--rca-arg={args.sources}",
    ]
    print(f"$ {' '.join(argv)}", file=sys.stderr)

    c = Client(argv, env)
    inv = args.investigation
    try:
        c.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "tinydemo-live", "version": "1"}})
        c.notify("notifications/initialized")

        brief = tool(c, "praxis_begin", {
            "investigation": inv, "trace": args.trace, "sources": args.sources,
            "summary": "Reproducible scripted run of data/failing-trace.json"})
        print(f"phase 1: {brief['brief']['incident']}", file=sys.stderr)

        cands = [e["entity"] for e in
                 brief["brief"]["candidate_entities_ranked_by_failing_hops"]][:5]
        tool(c, "praxis_select", {"investigation": inv, "candidates": cands})

        verdicts = []
        for _ in range(12):  # hard stop: never more entities than this
            try:
                data = tool(c, "praxis_next", {"investigation": inv})
            except RuntimeError as exc:
                # The server's own termination signal: the queue it holds is drained.
                if "Q is empty" in str(exc):
                    break
                raise
            entity = data.get("entity")
            if not entity:
                break
            blocks = find_blocks(data.get("regions", {}))
            fhops = data.get("failing_hops", 0)

            for action, block, target, insight in step_plan(blocks):
                args_step = {"investigation": inv, "entity": entity, "action": action,
                             "block": block, "insight": insight}
                if target:
                    args_step["target"] = target
                tool(c, "praxis_step", args_step)

            role = "Primary Failure" if fhops else "Symptom Only"
            if fhops:
                reasoning = (
                    f"{entity} lands {fhops} failing hop(s); the hop maps to "
                    f"{data.get('failing_method')} at {data.get('file')}:{data.get('line')} "
                    "and the recorded traversal shows the method has no branch of its own, "
                    "so the failure is produced here and not merely passed on.")
            else:
                reasoning = (
                    f"{entity} lands 0 failing hops: it only reports what its dependency "
                    "returned, so it is a symptom and not a cause. Judged on observability, "
                    "which is what the server directed when no regions were available.")
            tool(c, "praxis_judge", {"investigation": inv, "entity": entity,
                                     "role": role, "reasoning": reasoning})
            tool(c, "praxis_update_queue", {"investigation": inv, "entity": entity})
            verdicts.append((entity, role))

        rep = tool(c, "praxis_report", {"investigation": inv})
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        transcript = c.transcript
        c.close()
        # Write the transcript even when the run failed: a partial transcript is
        # the only way to see where the state machine refused to go on.
        os.makedirs(args.outdir, exist_ok=True)
        with open(os.path.join(args.outdir, "live-session.json"), "w") as fh:
            json.dump({"policy": "scripted (see scripts/praxis_client.py)",
                       "investigation": inv,
                       "verdicts": locals().get("verdicts", []),
                       "error": locals().get("error"),
                       "report": locals().get("rep"),
                       "transcript": transcript}, fh, indent=1)

    print(f"\nverdicts: {', '.join(f'{e} = {r}' for e, r in verdicts)}")
    print(f"coverage: {json.dumps(rep['coverage'])}")
    print(f"primary_failures={rep['primary_failures']}  symptom_only={rep['symptom_only']}")
    print(f"transcript: {os.path.join(args.outdir, 'live-session.json')} "
          f"({len(transcript)} messages)")


if __name__ == "__main__":
    main()
