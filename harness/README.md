# harness

ffmpeg scripts, the reference video, and generated failure-variant clips used
to drive demo scenarios. Not a deployed service — nothing here runs on
Cloud Run.

## Generating variants

1. Put a clean clip (60-90s, visible motion) at `reference/reference.mp4`
   (see `reference/README.md`).
2. Run:

   ```
   python3 harness/generate_variants.py
   ```

Writes `variants/macroblocking.mp4`, `variants/freeze.mp4`,
`variants/audio_drift.mp4`, and `variants/manifest.json` (ground truth for
evaluating the diagnoser — artifact_class, degradation start time, and
expected root cause per variant). All three are clean for the first 20s,
then degrade, so the transition is visible.

Safe to re-run any time — it regenerates all outputs from scratch.
