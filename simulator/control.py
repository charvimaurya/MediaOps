"""
Control surface: actions with genuinely mechanical, layered effects on
the pipeline's state (see simulator/failures.py's MediaState fields
encoder_fault / network_fault / bitrate_factor / active_output), rather
than clearing an "injected fault" flag wholesale. Every method here
mutates the SAME fields simulator/telemetry.py already publishes, so the
correct action for a fault genuinely restores telemetry, and an
incorrect one genuinely does not -- there is no shortcut that just marks
the incident fixed.

PipelineControl takes a "pipeline handle" (anything with .state_lock,
.current_state, .restart_ffmpeg()) so tests can inject a fake one and run
without real FFmpeg, per the test brief.
"""

import logging
import time
from typing import Optional, Tuple

from simulator import pipeline as real_pipeline
from simulator import backup as backup_module
from simulator.failures import compute_telemetry_fields
from simulator.telemetry import update_metrics
from agent.metrics import ACTIVE_ENCODER

logger = logging.getLogger(__name__)

RESTART_TIMEOUT_SECONDS = 2.0
# 0.3 (not 0.5, reduce_bitrate's own default) -- deliberately deeper, so
# failover reliably clears a network fault reduce_bitrate's default only
# partially relieves. See simulator/failures.py's network-layer formula.
SAFE_BITRATE_FACTOR = 0.3

# Mirrors simulator/pipeline.py's STREAM_NAME/ENCODER_NAME -- hardcoded
# here (rather than read off the pipeline handle) so update_metrics()
# still works against a fake handle in tests.
_STREAM_NAME = "main_stream"
_ENCODER_NAME = "encoder_01"


class PipelineControl:
    def __init__(self, pipeline_handle=None, backup_instance=None):
        self._pipeline = pipeline_handle or real_pipeline
        self._backup = backup_instance or backup_module.get_backup()

    # ------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------

    def _active_state(self, state):
        """Given the primary state (already under the lock), return
        whichever MediaState instance is the ACTIVE one right now."""
        if state.active_output == "backup":
            return self._backup.state
        return state

    def _refresh(self, primary_state) -> None:
        """Recompute exposed telemetry for whichever instance is active,
        writing the result onto primary_state (the object
        simulator/telemetry.py publishes from) -- same derivation
        telemetry_loop uses each tick, invoked immediately here so an
        action's effect is visible right away rather than waiting up to
        TELEMETRY_INTERVAL seconds."""

        if primary_state.active_output == "backup":
            instance = self._backup.state
            fields = compute_telemetry_fields(
                encoder_fault=instance.encoder_fault,
                network_fault=primary_state.network_fault,
                bitrate_factor=primary_state.bitrate_factor,
                real_cpu=self._backup.baseline_cpu,
                real_memory=self._backup.baseline_memory,
            )
        else:
            fields = compute_telemetry_fields(
                encoder_fault=primary_state.encoder_fault,
                network_fault=primary_state.network_fault,
                bitrate_factor=primary_state.bitrate_factor,
                real_cpu=None,
                real_memory=None,
            )
        for key, value in fields.items():
            setattr(primary_state, key, value)

        # Push into the actual exported Prometheus gauges immediately --
        # otherwise the fix is only visible on telemetry_loop's next
        # tick (up to TELEMETRY_INTERVAL seconds later), since that's
        # normally the only caller of update_metrics().
        update_metrics(_STREAM_NAME, _ENCODER_NAME, primary_state)

    # ------------------------------------------------------------
    # actions
    # ------------------------------------------------------------

    def restart_encoder(self) -> Tuple[bool, str]:
        start = time.monotonic()

        with self._pipeline.state_lock:
            primary_state = self._pipeline.current_state
            target = self._active_state(primary_state)
            restarting_backup = primary_state.active_output == "backup"

        try:
            if restarting_backup:
                process_ok = self._backup.restart()
            else:
                process_ok = self._pipeline.restart_ffmpeg()
        except Exception as exc:
            logger.exception("restart_encoder: exception restarting process")
            return False, f"encoder restart failed: {exc}"

        elapsed = time.monotonic() - start
        if elapsed > RESTART_TIMEOUT_SECONDS:
            return False, f"encoder restart exceeded {RESTART_TIMEOUT_SECONDS}s budget ({elapsed:.2f}s)"
        if not process_ok:
            return False, "encoder restart failed: new process did not come up"

        with self._pipeline.state_lock:
            primary_state = self._pipeline.current_state
            target = self._active_state(primary_state)
            # Resets encoder-layer state only -- queue depth / resource
            # load / dropped-frame accumulator all live in encoder_fault,
            # and a fresh process has none of them. Does NOT touch
            # network_fault (restarting an encoder does not fix a
            # degraded network) or bitrate_factor (that's
            # reduce_bitrate()'s own concern, a deliberate operator
            # choice a raw restart shouldn't silently undo).
            target.encoder_fault = "healthy"
            self._refresh(primary_state)

        return True, f"encoder restarted in {elapsed:.2f}s"

    def reduce_bitrate(self, factor: float = 0.5) -> Tuple[bool, str]:
        if not (0.0 <= factor <= 1.0):
            return False, f"invalid bitrate factor {factor!r}, must be within [0.0, 1.0]"

        with self._pipeline.state_lock:
            primary_state = self._pipeline.current_state
            primary_state.previous_bitrate_factor = primary_state.bitrate_factor
            primary_state.bitrate_factor = factor
            self._refresh(primary_state)

        return True, f"bitrate reduced to {factor:.2f}x nominal (was {primary_state.previous_bitrate_factor:.2f}x)"

    def switch_backup(self) -> Tuple[bool, str]:
        with self._pipeline.state_lock:
            primary_state = self._pipeline.current_state
            already_backup = primary_state.active_output == "backup"

        started = self._backup.start()  # idempotent -- no-op if already running
        if not started:
            return False, "switch_backup failed: standby process did not come up"

        with self._pipeline.state_lock:
            primary_state = self._pipeline.current_state
            primary_state.active_output = "backup"
            self._refresh(primary_state)
            ACTIVE_ENCODER.set(1)

        if already_backup:
            return True, "already on backup output"
        return True, "switched active output to backup"

    def failover(self) -> Tuple[bool, str]:
        """Last resort: switch to backup if not already, restore bitrate
        to a conservative safe value, and restart whichever encoder is
        now active. Allowed to be blunt -- this is the fail-safe."""

        with self._pipeline.state_lock:
            primary_state = self._pipeline.current_state
            primary_state.active_output = "backup"
            primary_state.bitrate_factor = SAFE_BITRATE_FACTOR
            ACTIVE_ENCODER.set(1)

        ok, detail = self.restart_encoder()

        with self._pipeline.state_lock:
            primary_state = self._pipeline.current_state
            self._refresh(primary_state)

        if not ok:
            return False, f"failover: backup restart failed: {detail}"
        return True, f"failed over to backup with bitrate={SAFE_BITRATE_FACTOR:.2f}x ({detail})"
