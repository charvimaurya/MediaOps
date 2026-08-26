import pytest

from detector.detector import IncidentDetector
from detector.models import IncidentType, IncidentStatus

HEALTHY = {
    "fps": 30.0,
    "cpu_usage": 45.0,
    "memory_usage": 50.0,
    "bitrate": 5.2,
    "encoding_latency": 42.0,
    "dropped_frames": 0.2,
    "packet_loss": 0.1,
    "network_latency": 30.0,
    "encoder_status": 1,
}

OVERLOAD = {**HEALTHY, "cpu_usage": 97.0, "encoding_latency": 190.0, "fps": 18.0}
FAILURE = {**HEALTHY, "encoder_status": 0, "fps": 0.0, "bitrate": 0.0}


class FakePrometheusClient:
    """No network, no sleeping -- readings are queued up front and
    returned one per get_telemetry() call. An entry of None means "raise"."""

    def __init__(self, readings):
        self._readings = list(readings)

    def get_telemetry(self):
        reading = self._readings.pop(0)
        if reading is None:
            raise ConnectionError("simulated Prometheus outage")
        return reading


def make_detector(readings):
    fake_client = FakePrometheusClient(readings)
    incidents = []
    detector = IncidentDetector(fake_client, on_incident=incidents.append, poll_interval=0)
    return detector, incidents


def run_n(detector, n):
    for _ in range(n):
        detector.run_iteration()


def test_two_breaching_samples_create_nothing_third_creates_one():
    detector, incidents = make_detector([OVERLOAD, OVERLOAD, OVERLOAD])

    run_n(detector, 2)
    assert incidents == []

    run_n(detector, 1)
    assert len(incidents) == 1
    assert incidents[0].type == IncidentType.ENCODER_OVERLOAD


def test_one_breaching_failure_sample_creates_incident_immediately():
    detector, incidents = make_detector([FAILURE])

    run_n(detector, 1)
    assert len(incidents) == 1
    assert incidents[0].type == IncidentType.ENCODER_FAILURE
    assert incidents[0].severity.value == "CRITICAL"


def test_ten_consecutive_breaching_samples_create_exactly_one_incident():
    detector, incidents = make_detector([OVERLOAD] * 10)

    run_n(detector, 10)

    assert len(incidents) == 1
    assert incidents[0].incident_id == "INC-001"


def test_healthy_sample_between_breaches_resets_the_counter():
    detector, incidents = make_detector([OVERLOAD, OVERLOAD, HEALTHY, OVERLOAD, OVERLOAD])

    run_n(detector, 5)

    assert incidents == []  # never reached 3 consecutive


def test_healthy_sample_then_three_more_breaches_still_creates_incident():
    detector, incidents = make_detector([OVERLOAD, OVERLOAD, HEALTHY, OVERLOAD, OVERLOAD, OVERLOAD])

    run_n(detector, 6)

    assert len(incidents) == 1


def test_prometheus_error_mid_loop_does_not_raise_or_create_incident():
    detector, incidents = make_detector([OVERLOAD, None, OVERLOAD])

    # Should not raise despite the None (simulated outage) in the middle.
    run_n(detector, 3)

    # 2 real breaching samples total (the failed iteration contributes
    # nothing either way) -- still short of the required 3.
    assert incidents == []


def test_three_healthy_samples_set_candidate_clear_but_stay_open():
    detector, incidents = make_detector([FAILURE, HEALTHY, HEALTHY, HEALTHY])

    run_n(detector, 4)

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.status == IncidentStatus.OPEN
    assert incident.resolved_at is None
    assert incident.candidate_clear is True


def test_dedup_updates_existing_incident_instead_of_creating_second():
    detector, incidents = make_detector([OVERLOAD] * 5)

    run_n(detector, 5)

    assert len(incidents) == 1
    # breach_count on the stored incident reflects the latest sample, not
    # just the confirmation threshold.
    open_incident = list(detector._open_incidents.values())[0]
    assert open_incident.incident_id == incidents[0].incident_id


def test_force_clear_resets_state():
    detector, incidents = make_detector([OVERLOAD, OVERLOAD])
    run_n(detector, 2)
    assert incidents == []

    detector.force_clear()

    fresh_detector, fresh_incidents = make_detector([OVERLOAD, OVERLOAD, OVERLOAD])
    run_n(fresh_detector, 3)
    assert len(fresh_incidents) == 1


def test_reason_includes_triggering_values():
    detector, incidents = make_detector([OVERLOAD, OVERLOAD, OVERLOAD])
    run_n(detector, 3)
    reason = incidents[0].reason
    assert "cpu_usage=97.0" in reason
