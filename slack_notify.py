"""
Step 5 of Phase 2: post the agent's finished report to Slack.

Requires env var: SLACK_WEBHOOK_URL (the one you created earlier)
"""

import os
import requests

SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

def send_to_slack(report_text: str) -> None:
    """Posts the agent's final report text to your Slack channel."""
    payload = {"text": report_text}
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload)
    resp.raise_for_status()
    print(f"Posted to Slack -> status {resp.status_code}")