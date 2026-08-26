import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

from agent.recovery_metrics import compute_rto, compute_rpo
from simulator.output_accounting import OutputAccounting, SEGMENT_DURATION_SECONDS
from detector.models import Incident, IncidentType, Severity, IncidentStatus


def make_incident(created_at, resolved_at=None):
    return Incident(
        incident_id="INC-RM",
        type=IncidentType.ENCODER_OVERLOAD,
        severity=Severity.HIGH,
        reason="test",
        status=IncidentStatus.RESOLVED if resolved_at else IncidentStatus.OPEN,
        created_at=created_at,
        resolved_at=resolved_at,
        telemetry_snapshot={},
        breach_count=3,
    )


def _write_segment(output_dir: str, index: int, mtime: float, size: int = 1024) -> str:
    path = os.path.join(output_dir, f"segment_{index:05d}.ts")
    with open(path, "wb") as f:
        f.write(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_rto_matches_created_at_to_resolved_at():
    created = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    resolved = created + timedelta(seconds=6.35)
    incident = make_incident(created, resolved)

    assert compute_rto(incident) == 6.35


def test_rto_none_when_not_resolved():
    created = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    incident = make_incident(created, resolved_at=None)

    assert compute_rto(incident) is None


def test_rpo_seconds_equals_missing_segments_times_segment_duration():
    with tempfile.TemporaryDirectory() as output_dir:
        stream_start = time.time() - 20.0
        created = datetime.fromtimestamp(stream_start + 4, tz=timezone.utc)
        resolved = datetime.fromtimestamp(stream_start + 10, tz=timezone.utc)
        incident = make_incident(created, resolved)

        # Nothing written in [4,10] -- a total gap during the incident.
        accounting = OutputAccounting([output_dir], stream_start=stream_start)

        result = compute_rpo(incident, accounting)

        assert result is not None
        assert result.segments_missing == 3  # (10-4)/2
        assert result.seconds_affected == 3 * SEGMENT_DURATION_SECONDS


def test_rpo_zero_when_segments_kept_up_during_incident():
    with tempfile.TemporaryDirectory() as output_dir:
        stream_start = time.time() - 20.0
        created = datetime.fromtimestamp(stream_start + 4, tz=timezone.utc)
        resolved = datetime.fromtimestamp(stream_start + 10, tz=timezone.utc)
        incident = make_incident(created, resolved)

        for i in range(11):
            _write_segment(output_dir, i, mtime=stream_start + i * SEGMENT_DURATION_SECONDS)

        accounting = OutputAccounting([output_dir], stream_start=stream_start)
        result = compute_rpo(incident, accounting)

        assert result is not None
        assert result.segments_missing == 0
        assert result.seconds_affected == 0.0


def test_failed_safe_incident_computes_rpo_to_now_not_to_none():
    with tempfile.TemporaryDirectory() as output_dir:
        stream_start = time.time() - 10.0
        created = datetime.fromtimestamp(stream_start + 2, tz=timezone.utc)
        # resolved_at stays None -- this is what a FAILED_SAFE incident
        # looks like (see agent/master_agent.py: only the RESOLVED branch
        # sets it).
        incident = make_incident(created, resolved_at=None)

        accounting = OutputAccounting([output_dir], stream_start=stream_start)
        result = compute_rpo(incident, accounting)

        assert result is not None
        # window_end should be close to "now", not absent/degenerate
        window_end = datetime.fromisoformat(result.window_end)
        assert (datetime.now(timezone.utc) - window_end).total_seconds() < 2.0


def test_unavailable_accounting_returns_none_not_zero():
    created = datetime.now(timezone.utc) - timedelta(seconds=5)
    resolved = datetime.now(timezone.utc)
    incident = make_incident(created, resolved)

    result = compute_rpo(incident, accounting=None)

    assert result is None


def test_degenerate_window_returns_none():
    now = datetime.now(timezone.utc)
    incident = make_incident(created_at=now, resolved_at=now)  # zero-length window

    with tempfile.TemporaryDirectory() as output_dir:
        accounting = OutputAccounting([output_dir], stream_start=time.time())
        result = compute_rpo(incident, accounting)

    assert result is None


def test_method_string_describes_the_calculation():
    with tempfile.TemporaryDirectory() as output_dir:
        stream_start = time.time() - 10.0
        created = datetime.fromtimestamp(stream_start + 2, tz=timezone.utc)
        resolved = datetime.fromtimestamp(stream_start + 8, tz=timezone.utc)
        incident = make_incident(created, resolved)

        accounting = OutputAccounting([output_dir], stream_start=stream_start)
        result = compute_rpo(incident, accounting)

        assert "segment" in result.method.lower()
        assert str(result.segments_missing) in result.method
