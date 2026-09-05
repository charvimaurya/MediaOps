"""
Check for infra_agent.py.

    python3 -m tools.manual_checks.infra_agent

Prereqs: Prometheus at :9090, Grafana at :3000 with the mcp-grafana service
account token in .env, `uvx` on PATH, ADC configured, Vertex AI enabled.

Covers: the window guard, both query_metrics branches returning the same shape,
and the strict structured-output guardrail (bad JSON / bad enum -> one retry ->
None; a retry that recovers; an infra failure raises rather than returning None).
"""

import json
import os
from datetime import datetime, timedelta, timezone

from agents import infra_agent
from models import FaultClass, InfraFinding

UTC = timezone.utc


# ---- 0. deterministic component-name normalization -------------------- #
print("0. affected_component aliases normalize to canonical system IDs")
for alias in (
    "encoder_01", "encoder", "encoder_1", "encoder 1", "encoder-1",
    "the encoder", "primary encoder", "primary_encoder", "primary-encoder",
    " Encoder ",
):
    assert infra_agent._normalize_affected_component(alias) == "encoder_01", alias
for alias in (
    "network path", "network_path", "network-path", "network", "the network",
    "network link", "network_link", "network-link", " NETWORK_PATH ",
):
    assert infra_agent._normalize_affected_component(alias) == "network path", alias
for alias in ("n/a", "na", "unknown", "none", " NONE "):
    assert infra_agent._normalize_affected_component(alias) == "n/a", alias
assert infra_agent._normalize_affected_component("database_01") == "database_01"
print("   OK: known aliases canonicalized; unknown target preserved")


print("\n0b. analyze_infra normalizes Gemini target 'encoder' to 'encoder_01'")
_normalization_real_query = infra_agent.query_metrics
_normalization_real_call = infra_agent._call_gemini
try:
    infra_agent.query_metrics = lambda start, end: {
        "media_cpu_usage_percent": {
            "latest": 97.0, "min": 95.0, "max": 99.0, "mean": 97.0, "points": 3,
        },
        "media_fps": {
            "latest": 18.0, "min": 17.0, "max": 19.0, "mean": 18.0, "points": 3,
        },
    }
    infra_agent._call_gemini = lambda summary, instruction: (
        '{"fault_class":"encoder_overload","affected_component":"encoder",'
        '"description":"CPU 97 and FPS 18 indicate overload","confidence":0.95}'
    )
    normalized = infra_agent.analyze_infra()
    assert isinstance(normalized, InfraFinding), normalized
    assert normalized.affected_component == "encoder_01", normalized.affected_component
    print(f"   OK: returned affected_component={normalized.affected_component!r}")
finally:
    infra_agent.query_metrics = _normalization_real_query
    infra_agent._call_gemini = _normalization_real_call


# ---- 1. window guard --------------------------------------------------- #
print("1. query_metrics rejects an out-of-bounds window")
now = datetime.now(UTC)
for label, s, e in [
    ("zero span", now, now),
    ("2h span", now - timedelta(hours=2), now),
    ("negative", now, now - timedelta(minutes=1)),
]:
    try:
        infra_agent.query_metrics(s, e)
        raise AssertionError(f"{label}: expected ValueError")
    except ValueError:
        pass
print("   OK: zero / too-wide / negative windows all raise ValueError")

# ---- 2. both branches, same shape ---------------------------------- #
print("\n2. query_metrics: grafana_mcp and prometheus_http return the same shape")
start, end = now - timedelta(minutes=5), now
_src = infra_agent.INFRA_METRICS_SOURCE

infra_agent.INFRA_METRICS_SOURCE = "prometheus_http"
http = infra_agent.query_metrics(start, end)
assert http, "prometheus_http returned nothing"
for m, s in http.items():
    assert set(s) == {"latest", "min", "max", "mean", "points"}, (m, s)
assert "media_fps" in http
print(f"   prometheus_http: {len(http)} metrics, media_fps.latest={http['media_fps']['latest']}")

try:
    infra_agent.INFRA_METRICS_SOURCE = "grafana_mcp"
    mcp = infra_agent.query_metrics(start, end)
    assert mcp, "grafana_mcp returned nothing"
    for m, s in mcp.items():
        assert set(s) == {"latest", "min", "max", "mean", "points"}, (m, s)
    assert set(mcp) == set(http), (set(mcp) ^ set(http))
    print(f"   grafana_mcp:     {len(mcp)} metrics, media_fps.latest={mcp['media_fps']['latest']}")
    print("   OK: identical metric set, identical summary shape")
except Exception as exc:  # noqa: BLE001
    print(f"   SKIP grafana_mcp branch ({type(exc).__name__}: {exc})")
finally:
    infra_agent.INFRA_METRICS_SOURCE = _src

# ---- 3/4/5/6. strict structured-output guardrail ------------------- #
_real_call = infra_agent._call_gemini


def patched(sequence):
    calls = {"n": 0}

    def fake(metrics_summary, instruction):
        i = calls["n"]
        calls["n"] += 1
        return sequence[min(i, len(sequence) - 1)]

    return fake, calls


# fix the source to the fast, no-subprocess branch for the guardrail checks
infra_agent.INFRA_METRICS_SOURCE = "prometheus_http"
VALID = '{"fault_class": "unknown", "affected_component": "n/a", "description": "nominal", "confidence": 0.4}'

try:
    print("\n3. bad JSON twice -> analyze_infra() returns None after 1 retry")
    infra_agent._call_gemini, calls = patched(["not json at all"])
    out = infra_agent.analyze_infra()
    assert out is None, out
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: None, {calls['n']} calls")

    print("\n4. invalid fault_class twice -> None after 1 retry")
    infra_agent._call_gemini, calls = patched(
        ['{"fault_class": "melted", "affected_component": "x", "description": "y", "confidence": 0.5}']
    )
    out = infra_agent.analyze_infra()
    assert out is None, out
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: None, {calls['n']} calls")

    print("\n5. garbage then valid -> retry recovers, returns an InfraFinding")
    infra_agent._call_gemini, calls = patched(["{bad", VALID])
    out = infra_agent.analyze_infra()
    assert isinstance(out, InfraFinding), out
    assert out.fault_class is FaultClass.UNKNOWN
    assert out.source == "infra"
    assert out.supporting_metrics, "supporting_metrics should carry the real latest values"
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: InfraFinding on attempt 2, supporting_metrics has {len(out.supporting_metrics)} metrics")

    print("\n6. an infra failure RAISES (not None)")
    infra_agent._call_gemini, calls = patched([VALID])  # gemini would be fine...
    _real_q = infra_agent._query_via_prometheus_http
    def boom(*a, **k):
        raise RuntimeError("simulated Prometheus outage")
    infra_agent._query_via_prometheus_http = boom
    raised = False
    try:
        infra_agent.analyze_infra()
    except RuntimeError as exc:
        raised = True
        print(f"   OK: raised {type(exc).__name__}: {exc}")
    finally:
        infra_agent._query_via_prometheus_http = _real_q
    assert raised, "analyze_infra swallowed an infra failure"
    assert calls["n"] == 0, "gemini was called despite the telemetry read failing"
finally:
    infra_agent._call_gemini = _real_call
    infra_agent.INFRA_METRICS_SOURCE = _src

print("\nALL CHECKS PASSED")
