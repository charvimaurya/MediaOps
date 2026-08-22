"""
Runs the investigation agent, collects its final report, and posts it to Slack.

Run with: python3 run_test.py
"""

import asyncio
from datetime import datetime, timezone
from google.genai import types
from google.adk.runners import InMemoryRunner
from grafana_tools import build_investigation_agent
from slack_notify import send_to_slack

async def main():
    agent = build_investigation_agent()
    runner = InMemoryRunner(agent=agent, app_name="broadcast_ops_copilot")

    session = await runner.session_service.create_session(
        app_name="broadcast_ops_copilot", user_id="test_user"
    )

    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    query = (
        f"The current UTC time is {current_time}. "
        "Investigate the encoding-pipeline service for elevated error patterns "
        "in the last hour and tell me the likely root cause."
    )

    print(f"Asking agent: {query}\n")

    message = types.Content(role="user", parts=[types.Part(text=query)])

    final_report = ""

    async for event in runner.run_async(
        user_id="test_user",
        session_id=session.id,
        new_message=message,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    print(part.text)
                    final_report += part.text + "\n"

    if final_report.strip():
        print("\nSending final report to Slack...")
        send_to_slack(final_report.strip())
    else:
        print("\nNo final report text captured — skipping Slack post.")

if __name__ == "__main__":
    asyncio.run(main())