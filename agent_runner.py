"""
Reusable entry point for running the investigation agent — importable by
run_test.py (CLI) and api.py (the web backend) alike.
"""

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Optional, TypedDict

from google.genai import types
from google.adk.runners import InMemoryRunner

from grafana_tools import build_investigation_agent

DEFAULT_SERVICE = "encoding-pipeline"
DEFAULT_HINT = "Look for elevated error patterns in the last hour and tell me the likely root cause."


class InvestigationEvent(TypedDict, total=False):
    type: str  # "status" | "text" | "done"
    text: str
    report: str


def _friendly_tool_label(name: str) -> str:
    lname = name.lower()
    if name == "get_live_impact_for_region":
        return "Checking live viewer impact"
    if "loki" in lname or "log" in lname:
        return "Checking Grafana logs"
    if "trace" in lname:
        return "Checking traces"
    if "datasource" in lname:
        return "Looking up Grafana datasources"
    return name.replace("_", " ").capitalize()


async def stream_investigation(
    service: str = DEFAULT_SERVICE, hint: Optional[str] = None
) -> AsyncIterator[InvestigationEvent]:
    """Run one investigation, yielding events as the agent works:
    {"type": "status", "text": ...} for each tool call/response, and a
    final {"type": "done", "report": ...} once the agent finishes.

    Args:
        service: the service to investigate.
        hint: a scenario-specific question appended to the base query;
            defaults to a generic error-pattern investigation.
    """
    agent = build_investigation_agent()
    runner = InMemoryRunner(agent=agent, app_name="broadcast_ops_copilot")

    session = await runner.session_service.create_session(
        app_name="broadcast_ops_copilot", user_id="test_user"
    )

    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    question = hint or DEFAULT_HINT

    query = f"The current UTC time is {current_time}. Investigate the {service} service. {question}"

    message = types.Content(role="user", parts=[types.Part(text=query)])

    report_parts = []
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session.id,
        new_message=message,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_call:
                    yield {
                        "type": "status",
                        "text": f"🔍 {_friendly_tool_label(part.function_call.name)}...",
                    }
                elif part.function_response:
                    yield {
                        "type": "status",
                        "text": f"✅ {_friendly_tool_label(part.function_response.name)} complete",
                    }
                elif part.text:
                    report_parts.append(part.text)

    yield {"type": "done", "report": "".join(report_parts)}


async def run_investigation(service: str = DEFAULT_SERVICE, hint: Optional[str] = None) -> str:
    """Build the agent, run one investigation query, and return the full
    streamed response as a single string.

    Args:
        service: the service to investigate for elevated error patterns.
        hint: a scenario-specific question appended to the base query.

    Returns:
        The agent's final report text.
    """
    report = ""
    async for event in stream_investigation(service, hint):
        if event["type"] == "done":
            report = event["report"]
    return report


if __name__ == "__main__":
    print(asyncio.run(run_investigation()))
