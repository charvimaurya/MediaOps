import os
import signal
import subprocess
import threading
import time
import random

import psutil
from prometheus_client import start_http_server

from simulator.failures import (
    healthy_state,
)

from simulator.telemetry import update_metrics


# ============================================================
# CONFIGURATION
# ============================================================

VIDEO_PATH = os.path.join(
    os.path.dirname(__file__),
    "video.mov",
)

STREAM_NAME = "main_stream"
ENCODER_NAME = "encoder_01"

PROMETHEUS_PORT = 8000

TELEMETRY_INTERVAL = 5


# ============================================================
# GLOBAL PIPELINE STATE
# ============================================================

current_state = healthy_state()

state_lock = threading.Lock()

ffmpeg_process = None


# ============================================================
# FFmpeg
# ============================================================

def start_ffmpeg():

    global ffmpeg_process

    if not os.path.exists(VIDEO_PATH):
        raise FileNotFoundError(
            f"Video not found: {VIDEO_PATH}"
        )

    command = [
        "ffmpeg",

        # Suppress unnecessary output
        "-hide_banner",
        "-loglevel",
        "error",

        # Loop video continuously
        "-stream_loop",
        "-1",

        # Read at native speed
        "-re",

        "-i",
        VIDEO_PATH,

        # Don't generate an actual output file.
        # We are using FFmpeg as the media-processing workload.
        "-f",
        "null",
        "-",
    ]

    print("\nStarting FFmpeg media pipeline...")

    ffmpeg_process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    print(
        f"FFmpeg started with PID "
        f"{ffmpeg_process.pid}"
    )


# ============================================================
# REAL SYSTEM METRICS
# ============================================================

def get_real_cpu_usage():

    if ffmpeg_process is None:
        return 0.0

    try:

        process = psutil.Process(
            ffmpeg_process.pid
        )

        # CPU percentage since previous call
        return process.cpu_percent(
            interval=0.5
        )

    except (
        psutil.NoSuchProcess,
        psutil.AccessDenied,
    ):
        return 0.0


def get_real_memory_usage():

    if ffmpeg_process is None:
        return 0.0

    try:

        process = psutil.Process(
            ffmpeg_process.pid
        )

        memory = process.memory_percent()

        return memory

    except (
        psutil.NoSuchProcess,
        psutil.AccessDenied,
    ):
        return 0.0


# ============================================================
# TELEMETRY LOOP
# ============================================================

def telemetry_loop():

    global current_state

    while True:

        with state_lock:

            state = current_state

            # ------------------------------------------------
            # Healthy pipeline
            # ------------------------------------------------

            if state.failure_mode == "healthy":

                real_cpu = get_real_cpu_usage()
                real_memory = get_real_memory_usage()

                # Keep CPU within sensible demo range
                state.cpu_usage = max(
                    0,
                    min(100, real_cpu)
                )

                state.memory_usage = max(
                    0,
                    min(100, real_memory)
                )

                # Simulated media characteristics
                state.fps = 30 + random.uniform(
                    -0.3,
                    0.3
                )

                state.bitrate = 5.2 + random.uniform(
                    -0.1,
                    0.1
                )

                state.encoding_latency = 42 + random.uniform(
                    -4,
                    4
                )

                state.dropped_frames = max(
                    0,
                    0.2 + random.uniform(
                        -0.05,
                        0.05
                    )
                )

                state.packet_loss = max(
                    0,
                    0.1 + random.uniform(
                        -0.02,
                        0.02
                    )
                )

                state.network_latency = max(
                    0,
                    30 + random.uniform(
                        -3,
                        3
                    )
                )

                state.encoder_status = 1

            # ------------------------------------------------
            # Update Prometheus
            # ------------------------------------------------

            update_metrics(
                STREAM_NAME,
                ENCODER_NAME,
                state,
            )

        time.sleep(
            TELEMETRY_INTERVAL
        )


# ============================================================
# CLEAN SHUTDOWN
# ============================================================

def shutdown():

    global ffmpeg_process

    print("\nShutting down MediaOps...")

    if ffmpeg_process:

        try:

            ffmpeg_process.terminate()

            ffmpeg_process.wait(
                timeout=5
            )

        except subprocess.TimeoutExpired:

            ffmpeg_process.kill()

    print("Media pipeline stopped.")


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        """
========================================
       MEDIAOPS AI SIMULATOR
========================================
"""
    )

    print(
        f"Video: {VIDEO_PATH}"
    )

    print(
        f"Prometheus metrics:"
        f" http://localhost:{PROMETHEUS_PORT}/metrics"
    )

    print()

    # Start Prometheus HTTP server
    start_http_server(
        PROMETHEUS_PORT
    )

    # Start FFmpeg
    start_ffmpeg()

    # Handle Ctrl+C
    signal.signal(
        signal.SIGINT,
        lambda sig, frame: shutdown()
    )

    signal.signal(
        signal.SIGTERM,
        lambda sig, frame: shutdown()
    )

    # Start telemetry
    telemetry_thread = threading.Thread(
        target=telemetry_loop,
        daemon=True,
    )

    telemetry_thread.start()

    print(
        "Telemetry collection started."
    )

    print(
        "Media pipeline is HEALTHY."
    )

    print(
        "\nPress CTRL+C to stop.\n"
    )

    # Keep application alive
    while True:

        if (
            ffmpeg_process
            and ffmpeg_process.poll()
            is not None
        ):

            print(
                "WARNING: FFmpeg process stopped."
            )

            break

        time.sleep(1)

    shutdown()


if __name__ == "__main__":
    main()