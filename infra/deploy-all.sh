#!/usr/bin/env bash
set -euo pipefail

# Deploys every service to Cloud Run, in dependency order (backends before
# the console, which is the least likely to be needed by anything else).
# Requires: gcloud CLI authenticated, GCP_PROJECT_ID and GCP_REGION set.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/deploy-detector.sh"
"$SCRIPT_DIR/deploy-orchestrator.sh"
"$SCRIPT_DIR/deploy-diagnoser.sh"
"$SCRIPT_DIR/deploy-remediator.sh"
"$SCRIPT_DIR/deploy-mock-pipeline.sh"
"$SCRIPT_DIR/deploy-console.sh"
