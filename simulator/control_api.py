from fastapi import FastAPI

from simulator.failures import (
    healthy_state,
    encoder_overload,
    network_degradation,
    encoder_failure,
)

from simulator import pipeline


app = FastAPI(
    title="MediaOps Failure Control API",
    description="Controlled media infrastructure failure and recovery API",
    version="1.0.0",
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

    with pipeline.state_lock:

        pipeline.current_state = (
            encoder_overload()
        )

    return {
        "status": "failure_triggered",
        "failure": "encoder_overload",
    }


@app.post("/failure/network-degradation")
def trigger_network_degradation():

    with pipeline.state_lock:

        pipeline.current_state = (
            network_degradation()
        )

    return {
        "status": "failure_triggered",
        "failure": "network_degradation",
    }


@app.post("/failure/encoder-crash")
def trigger_encoder_crash():

    with pipeline.state_lock:

        pipeline.current_state = (
            encoder_failure()
        )

    return {
        "status": "failure_triggered",
        "failure": "encoder_failure",
    }


@app.post("/recovery/reset")
def reset_pipeline():

    with pipeline.state_lock:

        pipeline.current_state = (
            healthy_state()
        )

    return {
        "status": "recovered",
        "failure_mode": "healthy",
    }