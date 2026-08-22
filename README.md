# Broadcast Ops Copilot

An AI agent that investigates streaming/broadcast infrastructure incidents automatically — the moment an alert fires, it digs through logs, figures out the likely root cause with a confidence score, checks how many viewers and how much revenue are actually at stake, and posts a ready-made report to Slack. No engineer has to manually dig through dashboards first.

## The problem

Streaming and media companies run a lot of tech behind the scenes — video encoding, servers, live delivery pipelines. This tech breaks in small ways all the time, not just during huge live events. When something breaks, an alert fires right away — that part is fast. But figuring out *why* it broke is slow. The engineer on duty has to manually check several different tools (error logs, system speed, alerts) and piece it together themselves, which usually takes 20–30 minutes every single time.

**Who has this problem:** The on-call engineer — the person who gets paged when something breaks and has to investigate it. Their manager also feels it indirectly, since they need incident reports without doing the digging themselves. This happens daily for any company running streaming or broadcast infrastructure — it's a routine problem, not a rare one.

**What we're solving:** We're removing the slow "detective work" part of fixing a broken system. The engineer should spend their time deciding what to fix — not hunting for what broke and why.

**How we're solving it:** An AI agent that does the investigating automatically the moment an alert fires:

1. Alert fires — something breaks (same as today)
2. The agent wakes up and starts investigating on its own
3. It checks the error logs, system speed, and other signals — pulling this data directly from Grafana (the tool that already stores all this information)
4. It connects the dots and figures out the likely cause, with a confidence score
5. It writes a clear report explaining what happened
6. It posts that report to the team automatically (e.g. Slack), instead of a person writing it up

Before: alert → 20–30 minutes of manual digging → written report.
After: alert → agent investigates in seconds → ready-made explanation.

The agent is built using **Gemini** (for the reasoning) and connects to **Grafana** through a live connection called **MCP** — so it's not just describing what it could do, it's actually pulling and reading real data on its own.

### Why this matters (the data)

- **Incidents are slow and expensive to diagnose.** PagerDuty's 2026 State of AI-First Operations Report found 68% of organizations lose more than $300,000 per hour during IT incidents, and 34% lose at least $500,000 per hour — that's the cost of every extra minute spent figuring out why something broke, which is exactly the step this agent removes.
- **Viewers abandon fast, so diagnosis speed directly costs revenue.** More than 50% of viewers leave a live stream within 90 seconds of poor quality, and each additional second of startup delay increases abandonment by over 6% (Columbia University). Akamai found each rebuffering instance causes roughly 1% viewer abandonment — for one major broadcaster, that meant nearly 500,000 lost viewing hours and $85,000 in lost ad revenue per rebuffering event. A Conviva study found major streaming providers lost $2.16 billion in revenue in a single year due to video quality problems.
- **This is a known, tracked problem — not a rare one.** Standard incident SLA targets (e.g. resolving P1 incidents within 4 hours) show companies already formally track and budget for this delay as an accepted, recurring cost of doing business.

### Why not just use a generic AI SRE agent (Datadog, Grafana)?

A generic SRE agent treats every alert the same way — it looks at error rates and latency, full stop. It has no idea that Alert A is affecting a live championship final with 2 million concurrent viewers, while Alert B is affecting a rarely-watched title with 40 viewers. To that agent, both alerts just look like "error rate spike" — same investigation, same priority.

This agent adds a layer of context those tools don't have: **business/audience awareness**. It knows what's actually streaming right now, how many people are affected, and can say something like:

> "This alert is currently affecting your #1 live event with an estimated $X/minute in ad revenue at risk — investigate this before the other two alerts."

That's a genuinely different capability, not just a different UI on the same thing.

## How it works (architecture)

- **`grafana_tools.py`** — builds the ADK agent, connects it to Grafana via MCP (`mcp-grafana`, run through `uvx`), and to a local impact-check tool. The agent's instruction also has it end each report with a small structured block (root cause, confidence, evidence, region/impact) so the UI can render it cleanly.
- **`impact_tool.py`** — `get_live_impact_for_region(region)` reads `live_titles.json` (a small simulated dataset of what's currently live per region) and returns viewer/revenue impact. This is the only simulated input — the investigation and reasoning are 100% real.
- **`agent_runner.py`** — `run_investigation(service, hint)` and `stream_investigation(service, hint)`, the reusable entry points that build the agent, run one query, and return/stream the result. Different scenarios send different `service`/`hint` pairs, producing genuinely different investigations (different tools get called, different evidence comes back).
- **`slack_notify.py`** — posts the finished report to a Slack webhook.
- **`api.py`** — FastAPI backend. `POST /api/investigate` (single JSON response) and `POST /api/investigate/stream` (live NDJSON stream of tool-call status as the agent works, ending in a parsed report) both wrap `agent_runner.py`. Also serves the built React frontend as static files.
- **`frontend/`** — React + Vite + TypeScript UI (originally scaffolded in Google AI Studio). Lets you pick a scenario, simulate an outage, watch the agent's tool calls stream in live, and see the parsed incident report card.
- **`run_test.py`** — a standalone CLI script that runs one hardcoded investigation and posts it to Slack, useful for a quick sanity check without the UI.
- **`agent.py`** — an early bare Gemini connection test, not part of the main flow, kept for reference.

## Prerequisites

- Python 3.11+
- Node.js 20+ and npm
- [`uv`](https://docs.astral.sh/uv/) installed (provides the `uvx` command, which launches the Grafana MCP server)
- A Grafana instance with a service account token
- A Slack incoming webhook URL
- A Google Cloud project with Vertex AI (Gemini) access

## Setup

### 1. Environment variables

Create a `.env` file in the project root:

```
GRAFANA_URL=https://your-org.grafana.net
GRAFANA_SERVICE_ACCOUNT_TOKEN=your-grafana-token
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### 2. Python backend

Use an isolated virtual environment — this prevents globally installed Python packages from overriding the MCP version required by Google ADK.

```zsh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If you previously installed `mcp==2.*` outside the virtual environment, don't run this project with the global `python3` — use `.venv/bin/python` (or activate the environment) instead.

### 3. Frontend

```zsh
cd frontend
npm install
npm run build
cd ..
```

## Running it

**Quickest — CLI only (no UI):**

```zsh
set -a && source .env && set +a
python run_test.py
```

Runs one hardcoded investigation, prints the streamed response, and posts it to Slack.

**Full app (backend + built frontend, one process — closest to production):**

```zsh
set -a && source .env && set +a
.venv/bin/uvicorn api:app --reload --port 8080
```

Open **http://localhost:8080**. FastAPI serves both the React UI and the `/api/*` endpoints.

**Frontend with hot reload (for UI iteration):**

```zsh
# terminal 1
set -a && source .env && set +a
.venv/bin/uvicorn api:app --reload --port 8080

# terminal 2
cd frontend
npm run dev
```

Open **http://localhost:3000** — Vite proxies `/api/*` to the backend on port 8080.

In the app: pick a scenario from the dropdown, click **Simulate Outage** in the Viewer view, then **Inspect in Ops Copilot** to trigger a real investigation. Every click makes a real Grafana + Gemini call and a real Slack post.

## Deploying

See [DEPLOY.md](DEPLOY.md) for the Cloud Run deployment steps (building the Docker image, storing secrets, and the `gcloud run deploy` command).
