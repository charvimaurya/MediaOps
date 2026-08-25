from dataclasses import dataclass


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

    # Human-readable state
    failure_mode: str = "healthy"


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
    )