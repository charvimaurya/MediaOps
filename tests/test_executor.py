from datetime import datetime, timezone

from agent.executor import RealActionExecutor
from detector.models import Incident, IncidentType, Severity, IncidentStatus


def make_incident():
    return Incident(
        incident_id="INC-TEST",
        type=IncidentType.ENCODER_OVERLOAD,
        severity=Severity.HIGH,
        reason="test",
        status=IncidentStatus.OPEN,
        created_at=datetime.now(timezone.utc),
        resolved_at=None,
        telemetry_snapshot={},
        breach_count=3,
    )


class FakeControl:
    def __init__(self):
        self.calls = []
        self.restart_encoder_result = (True, "restarted")
        self.reduce_bitrate_result = (True, "reduced")
        self.switch_backup_result = (True, "switched")
        self.failover_result = (True, "failed over")

    def restart_encoder(self):
        self.calls.append("restart_encoder")
        return self.restart_encoder_result

    def reduce_bitrate(self, factor=0.5):
        self.calls.append("reduce_bitrate")
        return self.reduce_bitrate_result

    def switch_backup(self):
        self.calls.append("switch_backup")
        return self.switch_backup_result

    def failover(self):
        self.calls.append("failover")
        return self.failover_result


def test_each_action_name_routes_to_the_correct_control_method():
    control = FakeControl()
    executor = RealActionExecutor(control=control)
    incident = make_incident()

    for action in ("restart_encoder", "reduce_bitrate", "switch_backup", "failover"):
        control.calls.clear()
        executor.execute(action, incident)
        assert control.calls == [action]


def test_control_failure_returns_executed_false_no_exception():
    control = FakeControl()
    control.restart_encoder_result = (False, "process did not come up")
    executor = RealActionExecutor(control=control)
    incident = make_incident()

    result = executor.execute("restart_encoder", incident)

    assert result.executed is False
    assert "did not come up" in result.detail


def test_exception_inside_control_is_caught_and_becomes_failed_result():
    class ExplodingControl(FakeControl):
        def restart_encoder(self):
            raise RuntimeError("boom")

    executor = RealActionExecutor(control=ExplodingControl())
    incident = make_incident()

    result = executor.execute("restart_encoder", incident)  # must not raise

    assert result.executed is False
    assert "boom" in result.detail


def test_unknown_action_returns_executed_false_without_raising():
    control = FakeControl()
    executor = RealActionExecutor(control=control)
    incident = make_incident()

    result = executor.execute("nonexistent_action", incident)

    assert result.executed is False
    assert "unknown action" in result.detail
