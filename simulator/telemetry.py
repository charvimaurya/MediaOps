from prometheus_client import Gauge


# ============================================================
# MEDIA PIPELINE METRICS
# ============================================================

FPS = Gauge(
    "media_fps",
    "Current video frames per second",
    ["stream", "encoder"]
)

CPU_USAGE = Gauge(
    "media_cpu_usage_percent",
    "Encoder CPU utilization percentage",
    ["stream", "encoder"]
)

MEMORY_USAGE = Gauge(
    "media_memory_usage_percent",
    "Encoder memory utilization percentage",
    ["stream", "encoder"]
)

BITRATE = Gauge(
    "media_bitrate_mbps",
    "Current video bitrate in Mbps",
    ["stream", "encoder"]
)

ENCODING_LATENCY = Gauge(
    "media_encoding_latency_ms",
    "Video encoding latency in milliseconds",
    ["stream", "encoder"]
)

DROPPED_FRAMES = Gauge(
    "media_dropped_frames_percent",
    "Percentage of dropped frames",
    ["stream", "encoder"]
)

PACKET_LOSS = Gauge(
    "media_packet_loss_percent",
    "Network packet loss percentage",
    ["stream", "encoder"]
)

NETWORK_LATENCY = Gauge(
    "media_network_latency_ms",
    "Network latency in milliseconds",
    ["stream", "encoder"]
)

ENCODER_STATUS = Gauge(
    "media_encoder_status",
    "Encoder status: 1 healthy, 0 failed",
    ["stream", "encoder"]
)

# 1 = healthy
# 0 = unhealthy
PIPELINE_HEALTH = Gauge(
    "media_pipeline_health",
    "Overall media pipeline health",
    ["stream"]
)


def update_metrics(
    stream,
    encoder,
    state,
):
    """
    Push the current MediaState into Prometheus metrics.
    """

    labels = {
        "stream": stream,
        "encoder": encoder,
    }

    FPS.labels(**labels).set(state.fps)

    CPU_USAGE.labels(**labels).set(state.cpu_usage)

    MEMORY_USAGE.labels(**labels).set(state.memory_usage)

    BITRATE.labels(**labels).set(state.bitrate)

    ENCODING_LATENCY.labels(**labels).set(
        state.encoding_latency
    )

    DROPPED_FRAMES.labels(**labels).set(
        state.dropped_frames
    )

    PACKET_LOSS.labels(**labels).set(
        state.packet_loss
    )

    NETWORK_LATENCY.labels(**labels).set(
        state.network_latency
    )

    ENCODER_STATUS.labels(**labels).set(
        state.encoder_status
    )

    # Overall health
    healthy = (
        state.encoder_status == 1
        and state.fps >= 24
        and state.dropped_frames < 5
        and state.packet_loss < 5
    )

    PIPELINE_HEALTH.labels(
        stream=stream
    ).set(1 if healthy else 0)