#!/usr/bin/env python3
"""Generate data/failing-trace.json — the demo's OTLP/JSON input.

Writes to stdout. See data/PROVENANCE.md for why the values are what they are.

Two quirks are deliberate:
  * http.status_code is emitted as a JSON number, not a string (tinylink's
    parser rejects the spec-conformant string form);
  * the route pattern / handler come from the real example solution, so the
    trace links to real source lines.
"""
import json
import sys

TRACE_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
ROUTE = "/api/agency/{agencyId}/member"
TARGET = "/api/agency/9f1c/member"
NS = 1_000_000_000  # 1 s in nanoseconds


def attr(key, value):
    if isinstance(value, str):
        return {"key": key, "value": {"stringValue": value}}
    return {"key": key, "value": {"intValue": value}}  # number, not string


def span(sid, parent, name, kind, attrs, start_ms, end_ms, status=None):
    out = {
        "traceId": TRACE_ID,
        "spanId": sid,
        "parentSpanId": parent,
        "name": name,
        "kind": kind,
        "attributes": [attr(k, v) for k, v in attrs.items()],
        "startTimeUnixNano": start_ms * 1_000_000,
        "endTimeUnixNano": end_ms * 1_000_000,
    }
    if status is not None:
        out["status"] = {"code": status}
    return out


def resource(service, spans):
    return {
        "resource": {"attributes": [attr("service.name", service)]},
        "scopeSpans": [{"spans": spans}],
    }


def main():
    dashboard = [
        # the caller's inbound request (SERVER, kind=2 would fit; kept CLIENT-free)
        span("0000000000000001", "", "GET /api/dashboard", 3,
             {"http.method": "GET", "http.target": "/api/dashboard",
              "http.status_code": 500}, 1000, 1450, status=2),
        # the outbound call into Agency.API (CLIENT, kind=3)
        span("0000000000000002", "0000000000000001", "HTTP GET", 3,
             {"http.method": "GET", "http.route": ROUTE, "http.target": TARGET,
              "http.status_code": 503}, 1050, 1400, status=2),
    ]
    agency = [
        # the server-side span that actually failed (SERVER, kind=2)
        span("0000000000000003", "0000000000000002", "HTTP GET", 2,
             {"http.method": "GET", "http.route": ROUTE, "http.target": TARGET,
              "http.status_code": 503}, 1100, 1400, status=2),
    ]
    doc = {"resourceSpans": [
        resource("Dashboard.API", dashboard),
        resource("Agency.API", agency),
    ]}
    json.dump(doc, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
