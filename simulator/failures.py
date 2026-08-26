from dataclasses import dataclass
from typing import Optional


@dataclass
class MediaState:
    """
    Represents the current health of the media pipeline.
    """

    fps: float = 30.0
    cpu_usage: float = 45.0
    memory_usage: float = 50.0
    bitrate: float = 5.2
    encoding_latency: float = 42.0
    dropped_frames: float = 0.2
    packet_loss: float = 0.1
    network_latency: float = 30.0
    encoder_status: int = 1

    # Human-readable state (display only -- not read by any threshold logic)
    failure_mode: str = "healthy"

    # ------------------------------------------------------------
    # Layered fault model + control-surface state. Added so
    # simulator/control.py's actions can address the encoder layer and
    # the network layer independently, instead of the four factories
    # below being the only way to set state (which they still are for
    # the *initial* injected preset -- these fields are what a
    # PipelineControl action then mutates mechanically).
    # ------------------------------------------------------------
    encoder_fault: str = "healthy"      # "healthy" | "overload" | "failure"
    network_fault: bool = False          # shared delivery path, not per-instance
    bitrate_factor: float = 1.0          # 1.0 = full nominal bitrate
    previous_bitrate_factor: Optional[float] = None  # recorded by reduce_bitrate()
    active_output: str = "primary"       # "primary" | "backup"


def healthy_state():
    return MediaState(
        fps=30.0,
        cpu_usage=45.0,
        memory_usage=50.0,
        bitrate=5.2,
        encoding_latency=42.0,
        dropped_frames=0.2,
        packet_loss=0.1,
        network_latency=30.0,
        encoder_status=1,
        failure_mode="healthy",
        encoder_fault="healthy",
        network_fault=False,
        bitrate_factor=1.0,
        active_output="primary",
    )


def encoder_overload():
    """
    Simulates encoder resource saturation.
    """

    return MediaState(
        fps=18.0,
        cpu_usage=97.0,
        memory_usage=88.0,
        bitrate=3.8,
        encoding_latency=190.0,
        dropped_frames=8.2,
        packet_loss=0.2,
        network_latency=35.0,
        encoder_status=1,
        failure_mode="encoder_overload",
        encoder_fault="overload",
        network_fault=False,
        bitrate_factor=1.0,
        active_output="primary",
    )


def network_degradation():
    """
    Simulates network degradation.
    """

    return MediaState(
        fps=20.0,
        cpu_usage=55.0,
        memory_usage=52.0,
        bitrate=3.5,
        encoding_latency=65.0,
        dropped_frames=5.4,
        packet_loss=12.0,
        network_latency=420.0,
        encoder_status=1,
        failure_mode="network_degradation",
        encoder_fault="healthy",
        network_fault=True,
        bitrate_factor=1.0,
        active_output="primary",
    )


def encoder_failure():
    """
    Simulates complete encoder failure.
    """

    return MediaState(
        fps=0.0,
        cpu_usage=2.0,
        memory_usage=10.0,
        bitrate=0.0,
        encoding_latency=0.0,
        dropped_frames=100.0,
        packet_loss=0.0,
        network_latency=30.0,
        encoder_status=0,
        failure_mode="encoder_failure",
        encoder_fault="failure",
        network_fault=False,
        bitrate_factor=1.0,
        active_output="primary",
    )


def compute_telemetry_fields(
    encoder_fault: str,
    network_fault: bool,
    bitrate_factor: float,
    real_cpu: Optional[float] = None,
    real_memory: Optional[float] = None,
) -> dict:
    """
    Single source of truth for "what does telemetry look like given this
    fault combination". Pure function: no I/O, no state. Used by
    simulator/pipeline.py's telemetry loop each tick (for whichever
    encoder instance -- primary or backup -- is currently active) and
    directly by tests.

    The encoder layer and the network layer are computed independently
    and then combined (fps/dropped_frames take the worse of the two),
    which is what makes the required asymmetry hold: restart_encoder /
    switch_backup only ever change the encoder-layer inputs, and
    reduce_bitrate only ever relieves the network-layer (and, partially,
    the encoder-layer) inputs.
    """

    if encoder_fault == "failure":
        return {
            "fps": 0.0,
            "cpu_usage": 2.0,
            "memory_usage": 10.0,
            "bitrate": 0.0,
            "encoding_latency": 0.0,
            "dropped_frames": 100.0,
            "packet_loss": 12.0 if network_fault else 0.1,
            "network_latency": 420.0 if network_fault else 30.0,
            "encoder_status": 0,
        }

    if encoder_fault == "overload":
        # bitrate_factor gives only partial relief -- cpu_usage is
        # pinned above the is_healthy() threshold (90) no matter what,
        # so an encoder overload can never be resolved by reduce_bitrate
        # alone, only by restart_encoder / switch_backup.
        relief = 1.0 - bitrate_factor
        cpu_usage = max(91.0, 97.0 - relief * 6.0)
        encoding_latency = max(155.0, 190.0 - relief * 70.0)
        encoder_fps = min(30.0, 18.0 + relief * 8.0)
        encoder_dropped = max(0.2, 8.2 - relief * 4.0)
        memory_usage = 88.0
    else:
        cpu_usage = real_cpu if real_cpu is not None else 45.0
        memory_usage = real_memory if real_memory is not None else 50.0
        encoding_latency = 42.0
        encoder_fps = 30.0
        encoder_dropped = 0.2

    if network_fault:
        # Tuned (Phase: RPO/demo tuning) so reduce_bitrate's default
        # factor (0.5, see simulator/control.py) is deliberately
        # insufficient: relief=0.5 leaves fps=23 (<24) and
        # dropped_frames=5.5 (>=5), both still failing is_healthy() --
        # genuine partial relief (up from fps=16/dropped=8.0 at no
        # relief), just not enough. Only a deeper cut -- failover's
        # SAFE_BITRATE_FACTOR (0.3, relief=0.7) -- pushes both under
        # threshold (fps=25.8, dropped=4.5). This was chosen by solving
        # for values where 0.5 relief fails and 0.7 relief passes with
        # comfortable margin, not reverse-engineered from a desired
        # action sequence.
        relief = 1.0 - bitrate_factor
        packet_loss = max(0.1, 14.0 - relief * 14.0)
        network_latency = max(30.0, 480.0 - relief * 450.0)
        network_fps = max(16.0, 30.0 - (1.0 - relief) * 14.0)
        network_dropped = max(0.2, 8.0 - relief * 5.0)
    else:
        packet_loss = 0.1
        network_latency = 30.0
        network_fps = 30.0
        network_dropped = 0.2

    fps = min(encoder_fps, network_fps)
    dropped_frames = max(encoder_dropped, network_dropped)
    bitrate = round(5.2 * bitrate_factor, 3)

    return {
        "fps": round(fps, 2),
        "cpu_usage": round(cpu_usage, 2),
        "memory_usage": round(memory_usage, 2),
        "bitrate": bitrate,
        "encoding_latency": round(encoding_latency, 2),
        "dropped_frames": round(dropped_frames, 2),
        "packet_loss": round(packet_loss, 2),
        "network_latency": round(network_latency, 2),
        "encoder_status": 1,
    }
