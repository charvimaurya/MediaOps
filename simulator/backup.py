"""
Standby encoder.

Runs a real (but trivial) ffmpeg process against a synthetic color source
-- not a second encode of the real video. Two phases ago this was a
purely simulated standby with no real process at all; that was fine when
nothing needed to measure real output, but simulator/output_accounting.py
now requires segments_written to reflect files that actually exist, never
a fabricated count. A fake standby would either fabricate a "healthy"
segment count after switching to it, or leave the accounting gap open
forever even once the agent reports RESOLVED -- both wrong. A synthetic
64x64/5fps source keeps this cheap (negligible CPU/disk) while still
being a genuine process writing genuine segment files, so switching to it
mechanically produces real evidence of recovery.

Its own health state (encoder_fault) stays independent of the primary's --
nothing in this simulator ever injects a fault into the backup.
"""

import logging
import os
import subprocess
import threading
import time

from simulator.failures import MediaState

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "backup")
SEGMENT_DURATION_SECONDS = 2


class BackupEncoder:
    def __init__(self):
        self.state = MediaState(
            fps=30.0,
            cpu_usage=20.0,
            memory_usage=30.0,
            bitrate=5.2,
            encoding_latency=40.0,
            dropped_frames=0.1,
            packet_loss=0.1,
            network_latency=30.0,
            encoder_status=1,
            failure_mode="healthy",
            encoder_fault="healthy",
            network_fault=False,
            bitrate_factor=1.0,
            active_output="backup",
        )
        # Baseline readings used in place of psutil-measured values --
        # the synthetic source is cheap enough that a real measurement
        # would be noise, not signal.
        self.baseline_cpu = 20.0
        self.baseline_memory = 30.0

        self.output_dir = OUTPUT_DIR
        self._process = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True  # already running

            os.makedirs(self.output_dir, exist_ok=True)
            gop = 5 * SEGMENT_DURATION_SECONDS  # source is 5fps

            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-re", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=5",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
                "-f", "hls", "-hls_time", str(SEGMENT_DURATION_SECONDS), "-hls_list_size", "0",
                "-hls_segment_filename", os.path.join(self.output_dir, "segment_%05d.ts"),
                os.path.join(self.output_dir, "playlist.m3u8"),
            ]

            try:
                self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                logger.exception("BackupEncoder: failed to start")
                return False

            time.sleep(0.2)
            return self._process.poll() is None

    def restart(self) -> bool:
        with self._lock:
            if self._process is not None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                self._process = None
        return self.start()

    def stop(self) -> None:
        with self._lock:
            if self._process is not None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                self._process = None


_backup_instance = None
_backup_lock = threading.Lock()


def get_backup() -> BackupEncoder:
    global _backup_instance
    with _backup_lock:
        if _backup_instance is None:
            _backup_instance = BackupEncoder()
        return _backup_instance


def reset_backup() -> None:
    """Test/demo helper -- stops any running process and drops the
    singleton so the next get_backup() starts fresh."""
    global _backup_instance
    with _backup_lock:
        if _backup_instance is not None:
            _backup_instance.stop()
        _backup_instance = None
