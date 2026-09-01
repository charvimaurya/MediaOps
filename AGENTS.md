# MediaOps CoPilot — Agent Context (AGENTS.md)

Context for any AI coding assistant working in this repo. Read this before doing anything.

## THE CORE PRINCIPLE (never violate)

**AI supplies judgment. Deterministic code supplies authority.**

- AI (Gemini) may only observe, classify, and suggest. It never executes, never approves, never declares recovery.
- Plain deterministic Python is the only thing allowed to approve an action (Safety Gate), execute it (Control Plane), or declare success (Verification).
- Never let an agent decide whether an action is safe, and never let "the API returned success" count as recovery.

## How to work with me (I am a beginner)

1. When I ask you to build something, FIRST give a short plan and the files you'll touch. Do NOT write code until I say "go".
2. Build ONE small piece at a time — one file / one job.
3. After building, write a test or command that proves it works against the running simulator, run it, and show me the real output. Never say "this should work."
4. Explain what the code does in plain language.
5. Keep changes minimal and readable.

**Do NOT edit anything in `simulator/`.** It works and is protected. You may READ it, never modify it. If a task seems to need a simulator change, tell me instead.

## The simulator (already built — my input, do not change)

A fake broadcast pipeline producing REAL telemetry off a REAL ffmpeg workload. Faults are genuine state changes that move real numbers — a correct fix genuinely restores them, a wrong fix genuinely doesn't.

Run it:
```
uvicorn simulator.control_api:app --port 8001
```
Also starts ffmpeg + telemetry loop + metrics on :8000. Prometheus on :9090, Grafana in a container.

Endpoints / signals:
- `:8001/health`, `:8001/state`
- `:8001/failure/encoder-overload`, `/failure/network-degradation`, `/failure/encoder-failure` (POST)
- `:8001/recovery/reset` (POST)
- `:8000/metrics` (follows state within ~10s: 1 tick + 1 scrape)
- `:9090` PromQL

Golden signal: `media_pipeline_health` (1 healthy, 0 broken) = encoder_status==1 AND fps>=24 AND dropped<5 AND packet_loss<5.
Metric lag ~10s — detection persistence and verification windows must be built around it.

Remediation surface (`simulator/control.py`) — the ONLY real actions:
- `restart_encoder()`, `reduce_bitrate(0.5)`, `switch_backup()`, `failover()`

## Fixed remediation action enum (must match control.py)

`RESTART_ENCODER`, `REDUCE_PROFILE`, `SWITCH_SOURCE`, `FAILOVER`
The Remediation Agent may ONLY choose from these. The enum enforces this.

## The messy video (Vision Agent input)

`simulator/output/messy_video.mov` — 23s, real pixel damage in sections:
- 0–5s healthy | 5–11s overload (blocky) | 11–17s RGB shift | 17–23s failure (black)

Fault → section mapping the Vision Agent uses:
- overload → 5–11s | rgb_shift → 11–17s | encoder_failure → 17–23s

The Vision Agent samples the MIDDLE of the matching section. The video's evidence (pixels) and the telemetry's evidence (metrics) are SEPARATE, independent witnesses to the same fault — this is intentional (dual-domain evidence).

## The workflow

```
detect -> record -> orchestrate -> [vision || infra] -> aggregate -> retrieve(RAG)
       -> decide -> safety gate -> execute -> verify -> fallback? -> report -> close
```

- Detector: polls Prometheus health; threshold + persistence + dedup -> one AnomalyEvent.
- Incident Recorder: durable Firestore write; one active incident per problem; mints incident_id.
- Orchestrator: plain function; takes an existing incident_id and drives it through states. Built with fake stubs first, then stubs swapped for real one at a time.
- Vision Agent: Gemini reads a frame from the matching messy-video section -> VisionFinding.
- Infra Agent: Gemini + READ-ONLY bounded Prometheus/Grafana (via Grafana MCP) -> InfraFinding.
- Evidence Aggregator: lightweight deterministic gate — both findings present + valid + AGREE -> IncidentEvidence, else STOP.
- Knowledge Base + RAG: Vertex AI embeddings + cosine similarity in Python; relevance threshold; precedent only.
- Remediation Agent: Gemini picks ONE action from the fixed enum. Suggestion only.
- Safety Gate: DETERMINISTIC, NO AI. Allow-list, compatibility, confidence, budget, cooldown. Any failed check or error -> BLOCK. Fail-closed.
- Control Plane: only on ALLOW; maps enum -> control.py method; idempotency key.
- Verify Recovery: re-check BOTH video AND telemetry across a stable window. Execution success is NOT recovery.
- Fallback: one alternate action within budget, else stop cleanly.
- Report: one Slack message. KB writeback: store outcome only if verified.

## Key architecture facts

- Everything is keyed by `incident_id` (uuid4 hex, the Firestore doc id in the `incidents` collection). Every step reads/writes that incident's own document. Multiple incidents run in isolated lanes because of this.
- Firestore is authoritative workflow state; agent memory is not.
- All three agents default to `gemini-2.5-flash`, configurable per agent via env vars (VISION_MODEL, INFRA_MODEL, REMEDIATION_MODEL).

## Guardrail tiers

- Tier 1 (must be real): detection persistence; Vision/Infra strict schema validation; Safety Gate fail-closed; Control Plane idempotency; Verification needs real dual-domain evidence.
- Tier 2 (simple): confidence thresholds; RAG relevance threshold; basic budget/cooldown.
- Tier 3 (minimal honest stubs): fallback = one retry then stop; reporting = one Slack message; KB writeback = final outcome only.

## Tech stack

Python, Pydantic (contracts + validation), Google ADK (agent framework), Gemini via Vertex AI, Firestore (state + KB), Vertex AI embeddings + cosine similarity (RAG, no pgvector), Prometheus/Grafana (partner, read-only for agents, via Grafana MCP), Cloud Run (deploy), Slack webhook (reporting).

## Non-negotiable rules

- AI agents never have direct execution authority.
- Every action must pass the deterministic Safety Gate. Safety Gate is fail-closed.
- Control Plane accepts only Gate-approved actions. Executable actions come only from the fixed enum + control.py.
- Duplicate delivery must not cause duplicate execution (idempotency).
- Credentials never in source control, never given to Gemini.
- Grafana/Prometheus access for agents is read-only and bounded.
- Recovery requires BOTH video and infra evidence across a stable window.
- Historical precedent informs decisions but never authorizes execution.

## Current build state

Built and tested (real, integrated): Steps 1–9 — data contracts, detector, incident recorder, orchestrator skeleton, vision agent, infra agent, aggregator, knowledge base + RAG, remediation agent. Components are runnable standalone against an incident_id, reading inputs from and writing outputs to the incident's Firestore doc.

Next: Safety Gate (Step 10), Control Plane (Step 11), Verify Recovery (Step 12), then fallback/report/KB-writeback, full orchestrator wiring, UI, deploy.
