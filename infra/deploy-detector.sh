#!/usr/bin/env bash
set -euo pipefail

# Deploys the detector service (quality scoring worker) to Cloud Run.
# Requires: gcloud CLI authenticated, GCP_PROJECT_ID and GCP_REGION set.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="detector"
SOURCE_DIR="$ROOT_DIR/detector"

: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID before deploying}"
: "${GCP_REGION:?Set GCP_REGION before deploying}"

gcloud run deploy "$SERVICE_NAME" \
  --source "$SOURCE_DIR" \
  --project "$GCP_PROJECT_ID" \
  --region "$GCP_REGION" \
  --platform managed \
  --allow-unauthenticated \
  --quiet
