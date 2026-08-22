"""
FastAPI backend for the Ops Copilot frontend. Exposes the investigation
agent over HTTP (plain JSON and a streaming NDJSON variant) and serves the
built React app as static files, so the whole thing runs as a single
Cloud Run service.

Run locally with: uvicorn api:app --reload --port 8080
"""

import json
import os
import re
from typing import AsyncIterator, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_runner import DEFAULT_SERVICE, run_investigation, stream_investigation
from slack_notify import send_to_slack

app = FastAPI(title="Broadcast Ops Copilot API")

_REPORT_BLOCK_RE = re.compile(r"---REPORT---\s*(.*?)\s*---END REPORT---", re.DOTALL)


def _clean(value: Optional[str]) -> Optional[str]:
    if not value or value.strip().lower() == "unknown":
        return None
    return value.strip()


def parse_report(raw: str) -> dict:
    """Pull the structured ---REPORT--- block (see grafana_tools.py's
    instruction) out of the agent's response. Falls back to a bare
    confidence-% regex over the whole text if the block is missing, so the
    UI still gets something useful if the model doesn't comply exactly."""
    match = _REPORT_BLOCK_RE.search(raw)
    narrative = raw[: match.start()].strip() if match else raw.strip()

    root_cause = confidence = region = viewers_affected = revenue_at_risk = None
    evidence: list[str] = []

    if match:
        for line in match.group(1).splitlines():
            line = line.strip()
            if line.startswith("ROOT_CAUSE:"):
                root_cause = _clean(line.split(":", 1)[1])
            elif line.startswith("CONFIDENCE:"):
                val = line.split(":", 1)[1].strip().rstrip("%")
                if val.isdigit():
                    confidence = int(val)
            elif line.startswith("REGION:"):
                region = _clean(line.split(":", 1)[1])
            elif line.startswith("VIEWERS_AFFECTED:"):
                viewers_affected = _clean(line.split(":", 1)[1])
            elif line.startswith("REVENUE_AT_RISK_USD_PER_MIN:"):
                revenue_at_risk = _clean(line.split(":", 1)[1])
            elif line.startswith("-"):
                item = line.lstrip("- ").strip()
                if item:
                    evidence.append(item)

    if confidence is None:
        fallback = re.search(r"(\d{1,3})\s*%", raw)
        if fallback:
            confidence = int(fallback.group(1))

    return {
        "narrative": narrative,
        "root_cause": root_cause,
        "confidence": confidence,
        "evidence": evidence,
        "region": region,
        "viewers_affected": viewers_affected,
        "revenue_at_risk_usd_per_min": revenue_at_risk,
    }


class InvestigateRequest(BaseModel):
    service: str = DEFAULT_SERVICE
    hint: Optional[str] = None


class InvestigateResponse(BaseModel):
    report: str
    narrative: str
    root_cause: Optional[str]
    confidence: Optional[int]
    evidence: list[str]
    region: Optional[str]
    viewers_affected: Optional[str]
    revenue_at_risk_usd_per_min: Optional[str]
    posted_to_slack: bool
    slack_error: Optional[str] = None


@app.post("/api/investigate", response_model=InvestigateResponse)
async def investigate(
    payload: Optional[InvestigateRequest] = Body(default=None),
) -> InvestigateResponse:
    service = payload.service if payload else DEFAULT_SERVICE
    hint = payload.hint if payload else None

    try:
        report = await run_investigation(service=service, hint=hint)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Investigation failed: {e}")

    parsed = parse_report(report)

    posted_to_slack = False
    slack_error = None
    try:
        send_to_slack(report)
        posted_to_slack = True
    except Exception as e:
        slack_error = str(e)

    return InvestigateResponse(
        report=report, posted_to_slack=posted_to_slack, slack_error=slack_error, **parsed
    )


async def _investigate_and_notify(service: str, hint: Optional[str]) -> AsyncIterator[dict]:
    """Streams status events from the live agent run, then parses the
    final report, posts it to Slack, and yields one enriched 'done' event
    with everything the UI needs."""
    raw_report = ""
    async for event in stream_investigation(service=service, hint=hint):
        if event["type"] == "done":
            raw_report = event["report"]
            break
        yield event

    parsed = parse_report(raw_report)

    posted_to_slack = False
    slack_error = None
    try:
        send_to_slack(raw_report)
        posted_to_slack = True
    except Exception as e:
        slack_error = str(e)

    yield {
        "type": "done",
        "report": raw_report,
        "posted_to_slack": posted_to_slack,
        "slack_error": slack_error,
        **parsed,
    }


@app.post("/api/investigate/stream")
async def investigate_stream(payload: Optional[InvestigateRequest] = Body(default=None)):
    service = payload.service if payload else DEFAULT_SERVICE
    hint = payload.hint if payload else None

    async def event_source():
        try:
            async for event in _investigate_and_notify(service, hint):
                yield json.dumps(event) + "\n"
        except Exception as e:
            yield json.dumps({"type": "error", "message": f"Investigation failed: {e}"}) + "\n"

    return StreamingResponse(event_source(), media_type="application/x-ndjson")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


_FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")
if os.path.isdir(_FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
