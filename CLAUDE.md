# MediaOps AI

## What this is

A multi-agent system that watches a video stream the way a viewer sees it,
diagnoses which part of the delivery pipeline caused any quality drop, fixes
that part, and confirms the picture recovered.

Built for a hackathon. Judged on: technological implementation, design,
potential impact, quality of idea.

## Core loop

Quality drop detected -> metric written to Grafana -> alert fires ->
diagnoser correlates video frames with infrastructure telemetry ->
root cause with confidence -> remediator proposes a fix ->
human approves -> execute -> verify recovery -> write to knowledge base ->
report to Slack.

## Non-negotiable design rules

1. The orchestrator owns all state and enforces all time budgets in code.
   Never implement a timeout or a confidence threshold inside an LLM prompt.
   Agents are stateless functions the orchestrator calls.

2. The remediator can only execute actions from a fixed allowed set, and
   every action has a defined undo. It never composes arbitrary commands.

3. Nothing reaches the mock pipeline without a human approval step.

4. The diagnoser must return structured JSON with a non-empty evidence array.
   An answer with no evidence is rejected and treated as low confidence.

5. Prior state is captured before every execution. If verification fails
   within budget, roll back automatically.

6. The system is additive. If the agents fail entirely, existing Grafana
   alerting still works.

## Time budgets (enforced in the orchestrator)

| Stage    | Budget | On breach                       |
|----------|--------|---------------------------------|
| Detect   | 10s    | Fall back to threshold alerting |
| Diagnose | 30s    | Escalate with partial findings  |
| Propose  | 15s    | Escalate with diagnosis only    |
| Verify   | 60s    | Auto-rollback and escalate      |

## Confidence routing

- Above 0.85 and knowledge base match: propose known fix, one-click approve
- 0.60 to 0.85: propose, marked unverified, show full evidence
- Below 0.60 or budget breached: escalate with what was checked and ruled out

Never return nothing. A narrowed search space is still a useful result.

## Allowed remediation actions

| action_id        | Effect                             | Undo                        |
|------------------|------------------------------------|-----------------------------|
| scale_encoder    | Increase encoder replicas or CPU   | Scale to prior value        |
| drop_rendition   | Serve a lower rendition            | Restore rendition ladder    |
| failover_origin  | Switch to backup origin            | Switch back to primary      |
| rollback_config  | Revert to previous config version  | Re-apply newer version      |

## Incident document schema

Both frontend and backend depend on this. Do not change it without saying so
explicitly and updating every consumer in the same commit.

```
incident_id            string
status                 detecting | diagnosing | awaiting_approval |
                       executing | verifying | resolved | escalated | rolled_back
opened_at              timestamp
resolved_at            timestamp | null
aht_seconds            number | null
scenario_label         string          (demo only)

quality:
  metric_before        number
  metric_after         number | null
  artifact_class       macroblocking | freeze | audio_drift | unknown

diagnosis:
  root_cause           string
  confidence           number 0..1
  evidence             [{ source, query, observation }]   non-empty
  matched_incident_id  string | null
  duration_seconds     number

remediation:
  action_id            string | null
  prior_state          object | null
  proposed_at          timestamp | null
  approved_by          string | null
  executed_at          timestamp | null
  verified             boolean | null
```

## Knowledge base entry schema

```
incident_id
signature_text         plain-English sentence, used for embedding
signature_embedding    vector
root_cause
action_id
worked                 boolean
human_verdict          approved | rejected
resolved_in_seconds
```

Signatures are plain sentences, not feature vectors. Example:
"macroblocking artifact, encoder CPU above 95 percent, output bitrate dropped
60 percent, no recent deployment". Match with cosine similarity on the
embedding.

## Stack

- Reasoning: Gemini
- Orchestration: Google Cloud Agent Builder
- Telemetry: Grafana Cloud, read and write, MCP for agent access
- Quality measurement: ffmpeg with libvmaf, SSIM
- Failure generation: ffmpeg
- State: Firestore
- Services: Cloud Run
- Frontend: React
- Notifications: Slack incoming webhook

## Repo layout

```
harness/          ffmpeg scripts, reference video, generated variants
detector/         quality scoring worker
orchestrator/     webhook, workflow, budget enforcement, rollback
agents/
  diagnoser/      prompt, tool schemas
  remediator/     prompt, tool schemas, action set
mock-pipeline/    encoder, origin, packager stubs with control API
console/          React ops view
infra/            deploy scripts, Grafana dashboard JSON, alert rules
docs/             spec, architecture, demo runbook
```

## Conventions

- Every deployable service gets a Dockerfile and a deploy script in infra/
- Grafana dashboards and alert rules live in infra/ as committed JSON,
  never configured by hand in the UI only
- Secrets come from environment variables, never committed
- Each phase ends with a working, committed, demonstrable state

## Working style for this repo

- Build one scenario end to end before starting the others
- Prefer boring and working over clever and fragile
- Ask before adding a dependency or changing the incident schema
- If something in this file conflicts with what I ask for in a session,
  tell me rather than silently picking one