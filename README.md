# MediaOps-AI

A multi-agent system that watches a video stream, diagnoses which part of
the delivery pipeline caused a quality drop, proposes and (with human
approval) executes a fix, and verifies recovery. See [CLAUDE.md](CLAUDE.md)
for the full design.

Status: scaffolding only. Every service below is a health-check stub — no
business logic yet.

## Repo layout

```
harness/          ffmpeg scripts, reference video, generated variants
detector/         quality scoring worker            (Cloud Run service)
orchestrator/     webhook, workflow, budget enforcement, rollback (Cloud Run service)
agents/
  diagnoser/      prompt, tool schemas               (Cloud Run service)
  remediator/     prompt, tool schemas, action set    (Cloud Run service)
mock-pipeline/    encoder, origin, packager stubs with control API (Cloud Run service)
console/          React ops view                     (Cloud Run service)
infra/            deploy scripts, Grafana dashboard JSON, alert rules
docs/             spec, architecture, demo runbook
```

## Prerequisites

- Python 3.11+
- Node 20+
- Docker
- [gcloud CLI](https://cloud.google.com/sdk/docs/install), authenticated
  (`gcloud auth login`) with a GCP project that has Cloud Run enabled
- ffmpeg built with libvmaf (for `harness/`, once that's implemented)

## Setup

1. Copy the env template and fill in real values (see "Environment
   variables" below for where to get each one):

   ```
   cp .env.example .env
   ```

2. Install dependencies for local development. The root `requirements.txt`
   covers the full Python stack (FastAPI, google-adk, google-genai, mcp,
   etc.) for running orchestrator/agent code locally in one virtualenv:

   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   Each Python service also has its own minimal `requirements.txt`
   (currently just fastapi + uvicorn) — that's what its Docker image
   installs, kept separate so stub containers don't pull in dependencies
   the service doesn't use yet.

3. Install the console's dependencies:

   ```
   cd console && npm install
   ```

## Running a service locally

Each Python service (`detector/`, `orchestrator/`, `agents/diagnoser/`,
`agents/remediator/`, `mock-pipeline/`) is a FastAPI app with a `/health`
endpoint:

```
cd detector
uvicorn main:app --reload --port 8080
curl localhost:8080/health
```

Console (React + Vite, served in production by a small Express server so
Cloud Run has a `/health` endpoint too):

```
cd console
npm run dev          # dev server with hot reload
# or, to test the production server:
npm run build && npm start
curl localhost:8080/health
```

## Deploying

Each service has a matching script in `infra/`. All of them read
`GCP_PROJECT_ID` and `GCP_REGION` from the environment:

```
export GCP_PROJECT_ID=your-project-id
export GCP_REGION=us-central1

infra/deploy-detector.sh
infra/deploy-orchestrator.sh
infra/deploy-diagnoser.sh
infra/deploy-remediator.sh
infra/deploy-mock-pipeline.sh
infra/deploy-console.sh

# or all at once:
infra/deploy-all.sh
```

Each deploys via `gcloud run deploy --source <service-dir>`, which builds
the Dockerfile in that directory with Cloud Build and deploys it. After the
first deploy, copy the printed service URL into the matching `*_URL`
variable in `.env`.

## Environment variables

See `.env.example` for the full list with inline comments on where to get
each value. Summary:

| Variable | Where to get it |
|---|---|
| `GCP_PROJECT_ID` | GCP Console → project selector |
| `GCP_REGION` | Pick a region where Agent Builder is available, e.g. `us-central1` |
| `GOOGLE_APPLICATION_CREDENTIALS` | GCP Console → IAM & Admin → Service Accounts → Keys (local dev only) |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com) → Get API key |
| `GRAFANA_CLOUD_URL` | grafana.com → My Account → your stack's details |
| `GRAFANA_CLOUD_API_KEY` | Grafana Cloud → Administration → Service accounts |
| `SLACK_INCOMING_WEBHOOK_URL` | Slack → api.slack.com/apps → your app → Incoming Webhooks |
| `FIRESTORE_DATABASE_ID` | Firestore console (usually `(default)`) |
| `ORCHESTRATOR_URL`, `DETECTOR_URL`, `DIAGNOSER_URL`, `REMEDIATOR_URL`, `MOCK_PIPELINE_URL` | Printed by `gcloud run deploy` after first deploy of each service |
| `REFERENCE_VIDEO_PATH` | Local path to the reference video used by `harness/` |
