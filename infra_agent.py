"""
The Infra Agent -- real Gemini (via Vertex AI, using Google ADK).

Given an incident, it reads the live `media_*` telemetry over the incident's
bounded time window, sends the numbers to Gemini, and returns a schema-valid
`InfraFinding` naming the fault class:

    encoder_overload     cpu spike + fps drop + high encoding latency
    network_degradation  packet loss + high network latency
    encoder_failure      media_encoder_status == 0
    unknown              telemetry ambiguous / missing

Telemetry is read **through the Grafana MCP server** (`mcp-grafana`, read-only,
bounded) -- Grafana is genuinely in the runtime loop. The querying lives in one
isolated function, `query_metrics()`, with a `grafana_mcp` branch and a
`prometheus_http` branch selected by `INFRA_METRICS_SOURCE`; the rest of the
agent never changes when you swap them.

Gemini interprets the numbers. It never receives credentials, never issues a
query, never touches infrastructure (no `tools=` on the agent).

Tier 1 guardrail: strict Pydantic parse of Gemini's answer; ONE strict retry;
on repeated failure -> return None (caller must stop the diagnostic).

Contract of `analyze_infra()`:
  - returns a valid InfraFinding on success;
  - returns None if Gemini's output fails validation twice;
  - raises on infra failure (MCP down, bad window, Prometheus unreachable,
    Gemini API/auth/quota error) -- fail-closed at the caller.

Standalone:
    python3 infra_agent.py [encoder-overload | network-degradation | encoder-crash]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.runners import InMemoryRunner
from google.genai import types

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from models import FaultClass, Incident, InfraFinding

logger = logging.getLogger("infra_agent")

# --------------------------------------------------------------------------- #
# Config -- module constants, os.environ with defaults (matches vision_agent.py)
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "broadcast-ops-copilot")
VERTEX_LOCATION = (
    os.environ.get("GOOGLE_CLOUD_LOCATION")
    or os.environ.get("GCP_REGION")
    or "us-central1"
)
INFRA_MODEL = os.environ.get("INFRA_MODEL", "gemini-2.5-flash")

# --- telemetry source ---
INFRA_METRICS_SOURCE = os.environ.get("INFRA_METRICS_SOURCE", "grafana_mcp")

# grafana_mcp branch
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")
GRAFANA_SERVICE_ACCOUNT_TOKEN = os.environ.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")
GRAFANA_DATASOURCE_UID = os.environ.get("GRAFANA_DATASOURCE_UID", "efwuuzsss6ccgc")
MCP_GRAFANA_CMD = os.environ.get("MCP_GRAFANA_CMD", "uvx")
MCP_GRAFANA_ARGS = os.environ.get(
    "MCP_GRAFANA_ARGS",
    "mcp-grafana@1.3.0 -t stdio -enabled-tools prometheus -disable-write",
)

# prometheus_http branch (fallback)
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

# the read-only, bounded allow-list -- no arbitrary PromQL is ever built
INFRA_METRICS = [
    "media_pipeline_health",
    "media_encoder_status",
    "media_fps",
    "media_cpu_usage_percent",
    "media_memory_usage_percent",
    "media_bitrate_mbps",
    "media_dropped_frames_percent",
    "media_packet_loss_percent",
    "media_encoding_latency_ms",
    "media_network_latency_ms",
]

MAX_WINDOW_SECONDS = 3600      # a query wider than this is rejected
WINDOW_LOOKBACK_SECONDS = 30   # window start = anomaly.detected_at - this
STEP_SECONDS = 15             # matches the Prometheus scrape interval
QUERY_TIMEOUT_SECONDS = 20.0

MAX_ATTEMPTS = 2  # one call + one strict retry
_APP_NAME = "mediaops_infra_agent"


# --------------------------------------------------------------------------- #
# 1. query_metrics -- the isolated, swappable telemetry read
# --------------------------------------------------------------------------- #

def _summarise(values: list[tuple[float, str]]) -> Optional[dict]:
    """[[ts, "value"], ...] -> {latest, min, max, mean, points}; None if empty."""
    nums = []
    for _, raw in values:
        try:
            nums.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not nums:
        return None
    return {
        "latest": round(nums[-1], 3),
        "min": round(min(nums), 3),
        "max": round(max(nums), 3),
        "mean": round(sum(nums) / len(nums), 3),
        "points": len(nums),
    }


def query_metrics(start: datetime, end: datetime) -> dict[str, dict]:
    """
    Read the bounded `media_*` allow-list over [start, end] and return
    {metric: {latest, min, max, mean, points}} -- the SAME shape whichever
    source is used. Raises on a bad window or any read failure.
    """
    span = (end - start).total_seconds()
    if not (0 < span <= MAX_WINDOW_SECONDS):
        raise ValueError(
            f"telemetry window {span:.0f}s is out of bounds (0, {MAX_WINDOW_SECONDS}]"
        )

    if INFRA_METRICS_SOURCE == "grafana_mcp":
        return _query_via_grafana_mcp(start, end)
    if INFRA_METRICS_SOURCE == "prometheus_http":
        return _query_via_prometheus_http(start, end)
    raise ValueError(f"unknown INFRA_METRICS_SOURCE {INFRA_METRICS_SOURCE!r}")


# -- grafana_mcp branch ---------------------------------------------------- #

def _query_via_grafana_mcp(start: datetime, end: datetime) -> dict[str, dict]:
    if not GRAFANA_SERVICE_ACCOUNT_TOKEN:
        raise RuntimeError(
            "GRAFANA_SERVICE_ACCOUNT_TOKEN is not set -- needed for INFRA_METRICS_SOURCE=grafana_mcp"
        )
    return asyncio.run(_query_via_grafana_mcp_async(start, end))


async def _query_via_grafana_mcp_async(start: datetime, end: datetime) -> dict[str, dict]:
    params = StdioServerParameters(
        command=MCP_GRAFANA_CMD,
        args=shlex.split(MCP_GRAFANA_ARGS),
        env={
            "GRAFANA_URL": GRAFANA_URL,
            "GRAFANA_SERVICE_ACCOUNT_TOKEN": GRAFANA_SERVICE_ACCOUNT_TOKEN,
            "GRAFANA_ORG_ID": os.environ.get("GRAFANA_ORG_ID", "1"),
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        },
    )
    # mcp-grafana wants strict RFC3339 (no microseconds, 'Z' suffix)
    start_rfc = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_rfc = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out: dict[str, dict] = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=QUERY_TIMEOUT_SECONDS) as session:
            await session.initialize()
            for metric in INFRA_METRICS:
                result = await session.call_tool(
                    "query_prometheus",
                    {
                        "datasourceUid": GRAFANA_DATASOURCE_UID,
                        "expr": metric,
                        "queryType": "range",
                        "startTime": start_rfc,
                        "endTime": end_rfc,
                        "stepSeconds": STEP_SECONDS,
                    },
                )
                if getattr(result, "is_error", False):
                    raise RuntimeError(f"Grafana MCP query_prometheus failed for {metric}: {result.content}")
                payload = _mcp_result_payload(result)
                series = (payload or {}).get("data") or []
                if not series:
                    continue
                summary = _summarise([tuple(v) for v in series[0].get("values", [])])
                if summary is not None:
                    out[metric] = summary
    return out


def _mcp_result_payload(result) -> Optional[dict]:
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        return sc
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return None
    return None


# -- prometheus_http branch (fallback) ---------------------------------- #

def _query_via_prometheus_http(start: datetime, end: datetime) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for metric in INFRA_METRICS:
        qs = urllib.parse.urlencode({
            "query": metric,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": STEP_SECONDS,
        })
        url = f"{PROMETHEUS_URL.rstrip('/')}/api/v1/query_range?{qs}"
        try:
            with urllib.request.urlopen(url, timeout=QUERY_TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Prometheus query_range failed for {metric}: {exc}") from exc
        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query_range not successful for {metric}: {payload.get('error')}")
        results = payload.get("data", {}).get("result", [])
        if not results:
            continue
        summary = _summarise([tuple(v) for v in results[0].get("values", [])])
        if summary is not None:
            out[metric] = summary
    return out


# --------------------------------------------------------------------------- #
# 2. The ADK agent + the real Gemini call
# --------------------------------------------------------------------------- #

class _InfraResponse(BaseModel):
    """The exact JSON contract asked of Gemini. Validated strictly."""

    model_config = ConfigDict(extra="forbid")

    fault_class: FaultClass
    affected_component: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)


_INSTRUCTION = """You are the read-only MediaOps Infra Agent for a live video pipeline.
You are given REAL telemetry summaries (latest / min / max / mean over a bounded
window) for a set of `media_*` metrics. Interpret ONLY these numbers.

Classify the fault as EXACTLY ONE of:
- encoder_overload: CPU pinned high (media_cpu_usage_percent >= ~90) AND fps dropped
  (media_fps < 24) AND encoding latency very high (media_encoding_latency_ms > ~150);
  packet loss and network latency near-normal; media_encoder_status = 1.
- network_degradation: packet loss high (media_packet_loss_percent >= ~5) AND/OR
  network latency very high (media_network_latency_ms > ~150); CPU and memory
  near-normal; fps only mildly down; media_encoder_status = 1.
- encoder_failure: media_encoder_status = 0 (or media_fps = 0 AND media_bitrate_mbps = 0);
  media_dropped_frames_percent near 100.
- unknown: the telemetry does not clearly match a signature, or key metrics are missing.

Never name a specific fault class when the telemetry is missing or ambiguous --
use `unknown` with low confidence. Do not recommend any remediation.

Fields:
- fault_class: one of encoder_overload, network_degradation, encoder_failure, unknown
- affected_component: e.g. "encoder_01" or "network path"
- description: ONE sentence citing the actual numbers you were given
- confidence: a number between 0 and 1

Return ONLY the JSON object, no markdown, no commentary.
"""

_RETRY_SUFFIX = """

YOUR PREVIOUS RESPONSE FAILED VALIDATION: {error}
Return ONLY a JSON object with exactly these four keys and nothing else:
  "fault_class": one of encoder_overload, network_degradation, encoder_failure, unknown
  "affected_component": a short string
  "description": a short string
  "confidence": a number between 0 and 1
"""


def _build_agent(instruction: str) -> LlmAgent:
    return LlmAgent(
        name=_APP_NAME,
        model=Gemini(
            model=INFRA_MODEL,
            client_kwargs={
                "vertexai": True,
                "project": GCP_PROJECT_ID,
                "location": VERTEX_LOCATION,
            },
        ),
        instruction=instruction,
        output_schema=_InfraResponse,
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def _call_gemini(metrics_summary: str, instruction: str) -> str:
    """
    One real ADK runtime call to Gemini on Vertex AI. Returns the raw final text
    (JSON). This is the single seam a test monkey-patches.
    """
    agent = _build_agent(instruction)
    runner = InMemoryRunner(agent=agent, app_name=_APP_NAME)
    runner.session_service.create_session_sync(
        app_name=_APP_NAME, user_id="infra", session_id="infra"
    )

    message = types.Content(
        role="user",
        parts=[types.Part.from_text(
            text=f"Telemetry for the incident window:\n\n{metrics_summary}\n\n"
                 f"Classify the fault. Return ONLY the JSON."
        )],
    )

    final: Optional[str] = None
    for event in runner.run(user_id="infra", session_id="infra", new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            final = event.content.parts[0].text
    if not final:
        raise RuntimeError("Gemini returned no final response")
    return final


# --------------------------------------------------------------------------- #
# 3. analyze_infra -- orchestration + the strict guardrail
# --------------------------------------------------------------------------- #

def _format_summary(metrics: dict[str, dict], start: datetime, end: datetime) -> str:
    lines = [
        f"window: {start.astimezone(timezone.utc).isoformat()} .. "
        f"{end.astimezone(timezone.utc).isoformat()}  ({(end - start).total_seconds():.0f}s)",
        f"source: {INFRA_METRICS_SOURCE}",
        "",
        f"{'metric':<30}{'latest':>10}{'min':>10}{'max':>10}{'mean':>10}{'pts':>6}",
    ]
    for m in INFRA_METRICS:
        s = metrics.get(m)
        if s is None:
            lines.append(f"{m:<30}{'(no data)':>10}")
        else:
            lines.append(
                f"{m:<30}{s['latest']:>10}{s['min']:>10}{s['max']:>10}{s['mean']:>10}{s['points']:>6}"
            )
    return "\n".join(lines)


def analyze_infra(
    incident: Optional[Incident] = None,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Optional[InfraFinding]:
    """See module docstring for the contract."""
    now = datetime.now(timezone.utc)
    if start is not None and end is not None:
        pass
    elif incident is not None:
        start = incident.anomaly.detected_at - timedelta(seconds=WINDOW_LOOKBACK_SECONDS)
        end = now
    else:
        start = now - timedelta(seconds=120)
        end = now
    if (end - start).total_seconds() > MAX_WINDOW_SECONDS:
        start = end - timedelta(seconds=MAX_WINDOW_SECONDS)

    ctx = f" (incident {incident.incident_id})" if incident is not None else ""
    logger.info("infra%s: reading %d metrics via %s over %.0fs window",
                ctx, len(INFRA_METRICS), INFRA_METRICS_SOURCE, (end - start).total_seconds())

    metrics = query_metrics(start, end)  # raises on infra failure -> fail-closed
    summary = _format_summary(metrics, start, end)

    instruction = _INSTRUCTION
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = _call_gemini(summary, instruction)
        try:
            parsed = _InfraResponse.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            logger.warning("infra%s attempt %d: invalid response: %s", ctx, attempt, exc)
            instruction = _INSTRUCTION + _RETRY_SUFFIX.format(error=str(exc)[:300])
            continue

        logger.info("infra%s attempt %d: %s (confidence %.2f)",
                    ctx, attempt, parsed.fault_class.value, parsed.confidence)
        return InfraFinding(
            fault_class=parsed.fault_class,
            affected_component=parsed.affected_component,
            description=parsed.description,
            supporting_metrics={m: s["latest"] for m, s in metrics.items()},
            confidence=parsed.confidence,
            model=INFRA_MODEL,
            raw_response=raw,
        )

    logger.error("infra%s: %d invalid responses -- returning None (diagnostic should stop)",
                 ctx, MAX_ATTEMPTS)
    return None


# --------------------------------------------------------------------------- #
# Manual run
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    import time

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    fault_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if fault_arg:
        req = urllib.request.Request(f"http://localhost:8001/failure/{fault_arg}", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            print(">>> injected:", r.read().decode())
        print("    waiting 15s for the fault to reach Prometheus...")
        time.sleep(15)

    try:
        finding = analyze_infra()
        if finding is None:
            print("\nNone -- diagnostic should stop (Gemini output failed validation twice)")
        else:
            print()
            print(finding.model_dump_json(indent=2))
    finally:
        if fault_arg:
            req = urllib.request.Request("http://localhost:8001/recovery/reset", method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                print("\n>>> reset:", r.read().decode())
