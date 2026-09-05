
# MediaOps CoPilot

An autonomous incident-response system for live video streaming. When a stream breaks,
it detects the problem, diagnoses it, decides on a fix, executes it, and independently
verifies recovery — with no human in the loop for routine incidents.

## THE CORE PRINCIPLE (never violate this)

**AI supplies judgment. Deterministic code supplies authority.**

- AI (Gemini) may only **observe, classify, and suggest**. It never executes anything,
  never approves anything, and never declares recovery.
- Plain deterministic Python is the only thing allowed to **approve** an action
  (Safety Gate), **execute** it (Control Plane), or **declare success** (Verification).
- If you are ever tempted to let an agent decide whether an action is safe, or let
  "the API returned success" count as recovery — stop. That breaks the whole project.

## How I want you (Claude Code) to work with me

I am a beginner. Follow this loop for every task:

1. When I ask you to build something, **first give me a short plan and the files you'll
   touch. Do not write code until I say go.**
2. Build **one small piece at a time** — one file / one job.
3. After building, **write a test or command that proves it works against the running
   simulator, run it, and show me the real output.** Never say "this should work."
4. Explain what the code does in plain language. Assume I need to understand it.
5. Keep changes minimal and readable over clever.

**Do NOT edit anything in `simulator/`.** It works and is protected. You may READ it to
understand behaviour, but never modify it. If a task seems to need a simulator change,
tell me instead of changing it.

## The simulator (already built — this is my input, do not change it)

A fake broadcast pipeline that produces **real telemetry off a real ffmpeg workload**.
Faults are genuine state changes that move real numbers — so a correct fix genuinely
restores them and a wrong fix genuinely doesn't.

Run it (single process):

```
uvicorn simulator.control_api:app --port 8001
```

This also starts ffmpeg + the telemetry loop + the metrics server on :8000.
Prometheus runs on :9090, Grafana in a container.

Key endpoints / signals:

- `:8001/health`, `:8001/state` — instant current state
- `:8001/failure/encoder-overload`, `/failure/network-degradation`,
  `/failure/encoder-failure` — inject a fault (POST)
- `:8001/recovery/reset` — return to healthy
- `:8000/metrics` — Prometheus gauges (follows state within ~10s: 1 tick + 1 scrape)
- `:9090` — Prometheus; query with PromQL

**Golden detection signal:** `media_pipeline_health` (1 = healthy, 0 = broken).
It is `encoder_status==1 AND fps>=24 AND dropped<5 AND packet_loss<5`.

**Remediation surface** (`simulator/control.py`) — the ONLY real actions that exist:

- `restart_encoder()` — clears the encoder fault layer
- `reduce_bitrate(0.5)` — lowers bitrate
- `switch_backup()` — switch to the standby encoder
- `failover()` — backup + bitrate 0.3x + restart (blunt, last resort)

**Metric lag matters:** state changes show in `/state` instantly but in `/metrics`
within ~10s. Detection persistence windows and verification stable-windows must be
built around this lag.

## Fixed remediation action enum (must match control.py exactly)

`RESTART_ENCODER`, `REDUCE_PROFILE`, `SWITCH_SOURCE`, `FAILOVER`

The Remediation Agent may ONLY choose from these. It cannot invent commands. The enum
enforces this structurally.

## The workflow (what I'm building on top of the simulator)

```
detect → record → orchestrate → [vision ‖ infra] → aggregate → retrieve(RAG)
       → decide → safety gate → execute → verify → fallback? → report → close
```

- **Infra Health Monitor** — continuously polls Prometheus health; threshold + persistence + dedup → one AnomalyEvent.
- **Incident Recorder** — durable Firestore write; one active incident per problem.
- **Orchestrator** — plain function calling steps in order, persisting state to Firestore.
  (Built early with FAKE stub steps to test flow, then stubs swapped for real one at a time.)
- **Vision Agent** — Gemini reads a recent frame → structured VisionFinding (video symptom).
- **Infra Agent** — Gemini + READ-ONLY bounded Prometheus/Grafana → InfraFinding (fault class).
- **Evidence Aggregator** — both findings validated (schema/source/time) → IncidentEvidence.
- **Knowledge Base + RAG** — embed evidence, retrieve similar past incidents above a
  relevance threshold (pre-seeded with 3–5 cases).
- **Remediation Agent** — Gemini picks ONE action from the fixed enum. Suggestion only.
- **Safety Gate** — DETERMINISTIC, NO AI. Allow-list, compatibility, confidence, budget,
  cooldown. Any failed check or error → BLOCK. Fail-closed.
- **Control Plane** — only on ALLOW; maps enum → control.py method; idempotency key so a
  duplicate can't execute twice.
- **Verify Recovery** — re-check BOTH video frame AND telemetry across a stable window.
  Execution success is NOT recovery.
- **Fallback** — one alternate action within budget, else stop cleanly.
- **Report** — one Slack message summarising the incident.
- **KB writeback** — store outcome ONLY if actually verified.

## Guardrail tiers (where to spend effort)

- **Tier 1 (must be real, judges will probe):** detection persistence; Vision strict
  schema validation; Safety Gate fail-closed; Control Plane idempotency; Verification
  needs real evidence.
- **Tier 2 (simple, don't over-engineer):** confidence thresholds; RAG relevance
  threshold; basic budget/cooldown.
- **Tier 3 (minimal, honest stubs):** fallback = one retry then stop; reporting = one
  Slack message; KB writeback = store final outcome only.

## Tech stack

- Python, Pydantic (data contracts + validation)
- Google ADK (agent framework — satisfies "Google Agent Platform")
- Gemini via Vertex AI (Vision, Infra, Remediation agents)
- Firestore (authoritative workflow state)
- Vertex AI embeddings + cosine similarity in Python (RAG — no pgvector needed)
- Prometheus / Grafana (partner integration, read-only for agents)
- Cloud Run (deploy)
- Slack webhook (reporting)

## Non-negotiable rules

- AI agents never have direct execution authority.
- Every action must pass the deterministic Safety Gate. Safety Gate is fail-closed.
- Control Plane accepts only Gate-approved, baseline-confirmed actions.
- Executable actions come only from the fixed enum + control.py methods.
- Duplicate delivery must not cause duplicate execution (idempotency).
- Credentials never in source control, never given to Gemini.
- Grafana/Prometheus access for agents is read-only and bounded.
- Recovery requires BOTH video and infra evidence across the stable window.
- Firestore is authoritative state; agent memory is not.
- Historical precedent informs decisions but never authorizes execution.
