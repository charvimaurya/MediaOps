# MediaOps CoPilot — Three-Minute Demo Script

Target duration: **2:50–3:00**

Demo URL: **https://mediaops-copilot-agg6oaal4a-uc.a.run.app**

Grafana: **https://niftykangaroo3569.grafana.net/public-dashboards/7b6a53bf3e1e4985b22c006e3834179e**

This recording follows one real **Encoder Overload** incident. The Safety Gate moment demonstrates an `ALLOW` decision; it does not require manufacturing a blocked incident.

## Before recording

1. Confirm no incident is currently active.
2. Open the MediaOps CoPilot landing page in one tab.
3. Open Grafana in a second tab with **Last 15 minutes** and **5s refresh** selected.
4. Confirm the simulator begins healthy and Grafana has fresh samples.
5. Keep browser zoom around 90–100% so the video and workflow card fit together.
6. Hide notifications, bookmarks, credentials, and unrelated tabs.
7. Do one dry run. Do not use `/recovery/reset` in the recorded run.

Exact transition times may vary because detection waits for sustained evidence and Gemini calls are live. Follow the on-screen state rather than rushing to match timestamps precisely.

---

## 0:00–0:20 — The hook and problem

### On screen

- Start on the landing page with the product name visible.
- Scroll slightly to show the product framing, but do not click **Try it now** yet.

### Say

> When a live stream breaks, viewers leave—and revenue leaves with them. Today, an alert tells an engineer something is wrong, but a human still has to investigate, diagnose, and fix it. That often takes 15 to 30 minutes while the audience sees broken video. MediaOps CoPilot handles that response autonomously, in seconds.

### Recording note

Keep this opening direct. Establish the operational pain before explaining the implementation.

---

## 0:20–0:40 — What it is and why it is safe

### On screen

- Briefly show the **Detect → Diagnose → Fix → Verify** flow.
- Click **Try it now** near the end of this section.

### Say

> MediaOps CoPilot is autonomous incident response for live video, built with Gemini, Google Cloud, and Grafana. Its core principle is simple: AI supplies judgment—observing, diagnosing, and recommending—but deterministic code holds the authority to approve, execute, and verify every fix. Let me show you a real incident.

---

## 0:40–0:55 — Inject a real encoder overload

### On screen

- Point briefly to **Encoder Overload**.
- Click it exactly once.
- Keep the video and workflow visible together.

### Say

> I am injecting an encoder overload into the real FFmpeg-backed video pipeline. The stream begins healthy so we can watch the failure appear rather than starting with an already-broken picture.

### When the video becomes blocky, say

> Now the viewer sees macroblocking. The system does not react to one brief glitch—it first confirms that the fault remains sustained, preventing noisy or unnecessary incidents.

### If monitoring remains visible, say

> This short wait is deliberate. Telemetry updates, Prometheus scrapes it, and the always-on Infra Health Monitor requires persistent unhealthy readings before opening an incident.

---

## 0:55–1:25 — Detection and two independent AI witnesses

### On screen

- Wait for **Incident Detected**.
- When **Diagnosing** appears, keep both agent boxes visible.
- Let the boxes change from “inspecting” to their real Firestore-backed findings.

### When detection appears, say

> The Infra Health Monitor has confirmed sustained unhealthy telemetry. The incident is durably recorded in Firestore, and the Orchestrator starts the investigation automatically.

### While both agents run, say

> Two Gemini agents investigate independently and in parallel. The Vision Agent examines the actual video pixels. The Infra Agent reads bounded, read-only infrastructure telemetry. Neither is given the other agent’s answer.

### When both findings appear, say

> Vision finds the viewer-facing symptom: macroblocking. Infra independently classifies the underlying fault as an encoder overload affecting `encoder_01`, supported by high CPU and reduced frame rate. The two witnesses agree, so the deterministic Evidence Aggregator confirms the diagnosis.

### Recording note

Pause long enough for the findings to be readable. Do not narrate a confidence percentage unless that exact value appears on screen.

---

## 1:25–1:45 — Similar incidents and remediation proposal

### On screen

- Let **Evidence Confirmed** complete.
- Pause on **Similar Past Incidents**.
- Show **Remediation Proposed**, including the real enum action and rationale.

### Say

> The system searches its operational memory for similar incidents using Vertex AI embeddings and deterministic retrieval. It recalls how comparable overloads were resolved.

> The Remediation Agent combines that history with the current evidence and proposes a fix from a fixed, tested set of enum actions. It cannot invent a shell command, and its proposal still has no execution authority.

---

## 1:45–2:03 — The deterministic Safety Gate: ALLOW

### On screen

- Hold on the **Safety Gate** card.
- Keep the green `ALLOW` verdict and passed checks visible.
- Pause briefly so judges can read the checks.

### Say

> This is the most important guardrail. Before anything runs, a deterministic Safety Gate—with no Gemini and no LLM—checks the action allow-list, fault compatibility, approved target, evidence confidence, blast radius, action budget, and cooldown.

> Every check has explicitly passed, so the action receives `ALLOW`. Only this approved decision can reach the Control Plane. Missing data, malformed input, or any failed check would fail closed and prevent execution.

### Recording note

This demo shows the successful `ALLOW` path. Do not say that a BLOCK is being demonstrated. The visible passed checks establish why this action was authorized.

---

## 2:03–2:25 — Execute the fix and show Grafana

### On screen

- When **Executing Fix** appears, show the approved action.
- Switch to the already-open Grafana tab.
- Point to the health dip, CPU spike, and FPS drop, followed by recovery.
- Return to the workflow after roughly 8–10 seconds.

### Say

> The Control Plane now executes the Gate-approved action through the simulator’s narrow control endpoint. An atomic idempotency key ensures duplicate delivery cannot execute the same action twice.

> These are real infrastructure metrics reaching Grafana Cloud. Pipeline health dropped from one to zero, encoder CPU rose, and frame rate fell when the overload began. After the encoder restart, the graphs begin returning to healthy values.

### Recording note

Grafana is the observability layer; do not imply that it approves or executes the fix. Keep the fault and recovery inside the selected time range.

---

## 2:25–2:45 — Independently verify recovery

### On screen

- Return to **Verifying Recovery**.
- Show samples accumulating across the stable window if visible.
- Let the video return to healthy only after verified recovery appears.

### Say

> Execution success is not treated as recovery. After allowing telemetry to settle, Verify Recovery repeatedly checks pipeline health and key metrics across a sustained window. It also independently confirms that the video is visually normal.

> Both domains remain healthy, so recovery is positively proven—not assumed. Only now does the console return to the clean stream.

### If verification is still observing, say

> A single good sample is deliberately not enough. The system waits for sustained healthy evidence so a brief improvement or relapse cannot be called recovery.

---

## 2:45–3:00 — Closed, reported, and auditable

### On screen

- Show **Reported & Closed**, final status `CLOSED`, and the real MTTR.
- Briefly show the audit summary or **Download report** control.
- End with the product wordmark or public URL visible.

### Say

> Recovery is reported to Slack, and the verified outcome is written back to the Knowledge Base as operational memory. The incident reaches `CLOSED`, with every transition, decision, and piece of evidence preserved for audit.

> MediaOps CoPilot is an autonomous first responder for live media—intelligent where judgment helps, deterministic wherever authority matters. Thank you.

Hold the final screen silently for one or two seconds.

---

## Editing and delivery checklist

- Keep the video under three minutes; target **2:50–2:55** before the final hold.
- Show the real incident running instead of replacing it with static screenshots.
- Keep agent findings, Safety Gate `ALLOW`, Grafana graphs, verification evidence, and final MTTR readable.
- Use subtle zooms only for those important moments.
- Trim long silent waits, but retain enough detection and verification time to show their sustained windows are real.
- Never overlay fabricated metric values, confidence, accuracy, or timing. Narrate only what the recorded run shows.
- Add captions if useful: **Parallel AI diagnosis**, **Deterministic Safety Gate**, **Idempotent execution**, and **Dual-domain verification**.
- Blur browser profiles, credentials, terminal history, and unrelated tabs.
- Finish with the public Cloud Run URL or repository link so judges can try it.
