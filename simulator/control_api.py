import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import fields

from fastapi import FastAPI
from prometheus_client import start_http_server

from simulator.failures import (
    MediaState,
    healthy_state,
    encoder_overload,
    network_degradation,
    encoder_failure,
)

from simulator import pipeline


logger = logging.getLogger(__name__)


# ============================================================
# SHARED-STATE ACCESS
# ============================================================

def _apply_state(new_state: MediaState) -> None:
    """Copy every field of ``new_state`` onto the process-wide
    ``pipeline.current_state`` object *in place*, under
    ``pipeline.state_lock``.

    The previous implementation did ``pipeline.current_state = <factory()>``,
    rebinding the module attribute to a brand-new object. Anything holding
    a reference to the old object (and, when the API runs as its own OS
    process, the telemetry loop's entire module namespace) never saw the
    change. Mutating the existing instance keeps it the exact same object
    every other part of the process already references, so the telemetry
    loop picks the change up on its very next tick and pushes it to
    Prometheus.
    """

    with pipeline.state_lock:
        current = pipeline.current_state
        for field in fields(MediaState):
            setattr(current, field.name, getattr(new_state, field.name))


# ============================================================
# PROCESS WIRING
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run the media pipeline (ffmpeg + telemetry loop + ``:8000/metrics``)
    inside *this* process.

    A mutation through the control API is only "immediately visible to the
    telemetry loop" if the telemetry loop is running in the same process
    as the API. This mirrors ``simulator/pipeline.py``'s ``main()`` wiring
    (calling its existing helpers, changing nothing in that module) so the
    control API is a self-sufficient entry point:

        uvicorn simulator.control_api:app --port 8001

    Do NOT also run ``python3 -m simulator.pipeline`` separately -- that is
    a second process with its own ``current_state`` and its own attempt to
    bind ``:8000``, which is the exact split this fix removes.
    """

    try:
        start_http_server(pipeline.PROMETHEUS_PORT)
        logger.info(
            "Prometheus metrics on :%d/metrics", pipeline.PROMETHEUS_PORT
        )
    except OSError as exc:
        logger.warning(
            "Could not bind metrics server on :%d (%s) -- is a separate "
            "`python3 -m simulator.pipeline` still running? Mutations made "
            "here will NOT reach that process.",
            pipeline.PROMETHEUS_PORT,
            exc,
        )

    try:
        pipeline.start_ffmpeg()
    except FileNotFoundError as exc:
        logger.warning("ffmpeg not started: %s", exc)

    telemetry_thread = threading.Thread(
        target=pipeline.telemetry_loop,
        daemon=True,
    )
    telemetry_thread.start()
    logger.info("Telemetry loop started inside the control API process.")

    yield

    pipeline.shutdown()


app = FastAPI(
    title="MediaOps Failure Control API",
    description="Controlled media infrastructure failure and recovery API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": "mediaops-simulator",
    }


@app.get("/state")
def get_state():

    with pipeline.state_lock:

        state = pipeline.current_state

        return {
            "fps": state.fps,
            "cpu_usage": state.cpu_usage,
            "memory_usage": state.memory_usage,
            "bitrate": state.bitrate,
            "encoding_latency": state.encoding_latency,
            "dropped_frames": state.dropped_frames,
            "packet_loss": state.packet_loss,
            "network_latency": state.network_latency,
            "encoder_status": state.encoder_status,
            "failure_mode": state.failure_mode,
        }


@app.post("/failure/encoder-overload")
def trigger_encoder_overload():

    _apply_state(encoder_overload())

    return {
        "status": "failure_triggered",
        "failure": "encoder_overload",
    }


@app.post("/failure/network-degradation")
def trigger_network_degradation():

    _apply_state(network_degradation())

    return {
        "status": "failure_triggered",
        "failure": "network_degradation",
    }


@app.post("/failure/encoder-crash")
def trigger_encoder_crash():

    _apply_state(encoder_failure())

    return {
        "status": "failure_triggered",
        "failure": "encoder_failure",
    }


@app.post("/recovery/reset")
def reset_pipeline():

    _apply_state(healthy_state())

    return {
        "status": "recovered",
        "failure_mode": "healthy",
    }
