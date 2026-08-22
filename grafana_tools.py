"""
Step 2 of Phase 2 (extended): the investigation agent now has two tools —
Grafana (via MCP) for root-cause investigation, and a local impact-check
tool for prioritizing by viewer/revenue impact.
"""

import os
from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioConnectionParams
from mcp import StdioServerParameters
from impact_tool import get_live_impact_for_region

GRAFANA_URL = os.environ["GRAFANA_URL"]
GRAFANA_TOKEN = os.environ["GRAFANA_SERVICE_ACCOUNT_TOKEN"]

LOKI_DATASOURCE_NAME = "grafanacloud-niftykangaroo3569-logs"
LOKI_DATASOURCE_UID = "grafanacloud-logs"

def get_grafana_toolset() -> MCPToolset:
    return MCPToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uvx",
                args=["mcp-grafana"],
                env={
                    "GRAFANA_URL": GRAFANA_URL,
                    "GRAFANA_SERVICE_ACCOUNT_TOKEN": GRAFANA_TOKEN,
                },
            ),
            timeout=60,
        )
    )

def build_investigation_agent() -> Agent:
    return Agent(
        name="broadcast_ops_investigator",
        model="gemini-2.5-flash",
        instruction=(
            "You are an SRE assistant for a streaming platform. When asked to investigate "
            "an incident, follow these steps in order:\n\n"
            "1. INVESTIGATE: use the Grafana tools to search relevant logs and find the "
            "likely root cause, with a confidence estimate (as a percentage). Be specific "
            "and cite evidence.\n\n"
            "2. CHECK IMPACT: once you know which region is affected, call "
            "get_live_impact_for_region with that region to find out what's currently live "
            "there and how many viewers/how much revenue is at stake.\n\n"
            "3. PRIORITIZE: combine both findings into a final report that states the root "
            "cause AND why this incident does or doesn't need urgent attention right now, "
            "based on the real-world impact you found — not just the error severity.\n\n"
            "IMPORTANT — datasource: the account has multiple Loki datasources, including a "
            f"broken, unconfigured one named 'loki'. Always use the datasource named "
            f"'{LOKI_DATASOURCE_NAME}' (uid: '{LOKI_DATASOURCE_UID}') — never the plain 'loki' one.\n\n"
            "IMPORTANT — time format: whenever a tool requires a time range or timestamp, you MUST "
            "use a precise RFC3339 timestamp (e.g. 2026-08-20T13:00:00Z) — never relative words "
            "like 'now', 'today', or 'last hour' as literal argument values. The current UTC time "
            "will be given to you in the user's message — calculate start/end times by subtracting "
            "from it (e.g. one hour ago).\n\n"
            "IMPORTANT — fallback if the automatic error-pattern tool finds nothing: directly "
            "query Loki logs using a LogQL selector like {service=\"encoding-pipeline\"} for the "
            "same time window, count how many returned lines contain 'ERROR', and report that "
            "count and a sample of the error lines as your evidence.\n\n"
            "IMPORTANT — final answer format: write your narrative investigation as usual, "
            "then ALWAYS end your response with a machine-parsable block in exactly this "
            "format (plain text, no markdown code fences, no commentary inside it, use "
            "\"unknown\" for any field you couldn't determine):\n\n"
            "---REPORT---\n"
            "ROOT_CAUSE: <one-sentence likely root cause>\n"
            "CONFIDENCE: <integer 0-100, no percent sign>\n"
            "EVIDENCE:\n"
            "- <evidence bullet>\n"
            "- <evidence bullet>\n"
            "REGION: <region you checked impact for, or \"unknown\">\n"
            "VIEWERS_AFFECTED: <number, or \"unknown\">\n"
            "REVENUE_AT_RISK_USD_PER_MIN: <number, or \"unknown\">\n"
            "---END REPORT---"
        ),
        tools=[get_grafana_toolset(), get_live_impact_for_region],
    )