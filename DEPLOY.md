# Local development

Two ways to run it locally:

**Full stack via the backend (closest to production):**
```zsh
cd frontend && npm install && npm run build && cd ..
set -a && source .env && set +a
.venv/bin/uvicorn api:app --reload --port 8080
```
Open http://localhost:8080 — FastAPI serves the built React app and the `/api/investigate` endpoint from one process.

**Frontend with hot reload (faster iteration on UI):**
```zsh
# terminal 1
set -a && source .env && set +a
.venv/bin/uvicorn api:app --reload --port 8080

# terminal 2
cd frontend && npm run dev
```
Open http://localhost:3000 — Vite proxies `/api/*` to the backend on 8080 (see `frontend/vite.config.ts`).

# Deploying to Cloud Run

The `Dockerfile` is a two-stage build: Node builds the React app into static
files, then a Python image installs `requirements.txt`, copies those static
files in, and runs `uvicorn api:app` — serving the UI and the `/api/*`
endpoints as a single process, on one Cloud Run service.

## 1. Build and push the image

```zsh
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/broadcast-ops-copilot
```

## 2. Store secrets (don't pass tokens as plain env vars)

```zsh
printf '%s' "$GRAFANA_SERVICE_ACCOUNT_TOKEN" | gcloud secrets create grafana-token --data-file=-
printf '%s' "$SLACK_WEBHOOK_URL" | gcloud secrets create slack-webhook-url --data-file=-
```

If the secrets already exist, use `gcloud secrets versions add <name> --data-file=-` instead.

## 3. Deploy

```zsh
gcloud run deploy broadcast-ops-copilot \
  --image gcr.io/YOUR_PROJECT_ID/broadcast-ops-copilot \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GRAFANA_URL=https://your-org.grafana.net,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,GOOGLE_CLOUD_LOCATION=us-central1 \
  --set-secrets GRAFANA_SERVICE_ACCOUNT_TOKEN=grafana-token:latest,SLACK_WEBHOOK_URL=slack-webhook-url:latest
```

Notes:
- Replace `YOUR_PROJECT_ID` and `GRAFANA_URL` with your actual values.
- `--allow-unauthenticated` makes the demo publicly reachable — drop it (and use `gcloud run services proxy` or IAM invoker bindings) if this needs to stay private.
- The deploying service account needs the `Vertex AI User` role for Gemini calls to succeed, and `Secret Manager Secret Accessor` on the two secrets above.
- Each "Simulate Outage" → investigate click makes a real Grafana + Gemini call and a real Slack post — consider `--min-instances 0 --max-instances` limits to control cost on a public demo URL.
