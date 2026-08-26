"""
Scenario tests with a fake pipeline -- no real FFmpeg, no real Prometheus,
no real sleeping (detector polling and verification settling are both
driven manually/with settle_seconds=0).
"""

import tempfile
import threading
import time

import pytest

from agent.master_agent import MasterAgent, AgentState
from agent.diagnosis import StubDiagnosisProvider
from agent.knowledge import KnowledgeBase
from agent.executor import RealActionExecutor
from agent.verification import Verifier
from agent.guardrails import GuardrailEngine
from simulator.control import PipelineControl
from simulator.output_accounting import OutputAccounting
from simulator.failures import MediaState, healthy_state
from detector.detector import IncidentDetector
from demo.scenarios import SCENARIOS
from demo.runner import reset_all


class FakePipeline:
    def __init__(self):
        self.current_state = healthy_state()
        self.state_lock = threading.Lock()

    def restart_ffmpeg(self) -> bool:
        return True


class FakeBackup:
    def __init__(self):
        self.state = MediaState(
            fps=30.0, cpu_usage=20.0, memory_usage=30.0, bitrate=5.2,
            encoding_latency=40.0, dropped_frames=0.1, packet_loss=0.1,
            network_latency=30.0, encoder_status=1, failure_mode="healthy",
            encoder_fault="healthy", network_fault=False, bitrate_factor=1.0,
            active_output="backup",
        )
        self.baseline_cpu = 20.0
        self.baseline_memory = 30.0
        self.reset_calls = 0

    def start(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def reset(self) -> None:
        self.reset_calls += 1


class FakeTelemetryClient:
    def __init__(self, fake_pipeline):
        self._pipeline = fake_pipeline

    def get_telemetry(self) -> dict:
        with self._pipeline.state_lock:
            s = self._pipeline.current_state
            return {
                "fps": s.fps, "cpu_usage": s.cpu_usage, "memory_usage": s.memory_usage,
                "bitrate": s.bitrate, "encoding_latency": s.encoding_latency,
                "dropped_frames": s.dropped_frames, "packet_loss": s.packet_loss,
                "network_latency": s.network_latency, "encoder_status": s.encoder_status,
            }


def build_stack(accounting=None):
    fake_pipeline = FakePipeline()
    fake_backup = FakeBackup()
    telemetry_client = FakeTelemetryClient(fake_pipeline)

    control = PipelineControl(pipeline_handle=fake_pipeline, backup_instance=fake_backup)
    master_agent = MasterAgent(
        diagnosis_provider=StubDiagnosisProvider(),
        knowledge_base=KnowledgeBase(),
        action_executor=RealActionExecutor(control=control),
        verifier=Verifier(telemetry_client, settle_seconds=0),
        guardrail_engine=GuardrailEngine(cooldown_seconds=0),
        output_accounting=accounting,
    )
    detector = IncidentDetector(telemetry_client, on_incident=lambda incident: None, poll_interval=0)

    return fake_pipeline, fake_backup, detector, master_agent


def run_scenario_synchronously(scenario, fake_pipeline, detector, master_agent, backdate_seconds=0.0):
    """Drives detection deterministically (3 manual polls, matching
    REQUIRED_BAD_READINGS) instead of a real background thread, then
    calls the agent directly -- avoids real sleeping entirely.

    backdate_seconds artificially ages the incident's created_at after
    the detector creates it, purely so a synchronous test (which
    otherwise runs in near-zero wall-clock time) can still produce a
    genuine, non-degenerate RTO/RPO window -- it does not touch any
    production code's notion of "now"."""
    import datetime as _datetime
    from simulator import failures as failures_module

    factory = getattr(failures_module, scenario.fault_factory)
    with fake_pipeline.state_lock:
        fake_pipeline.current_state = factory()

    incident_holder = {}

    def capture(incident):
        incident_holder["incident"] = incident

    detector._on_incident = capture

    for _ in range(3):
        detector.run_iteration()

    incident = incident_holder.get("incident")
    assert incident is not None, "detector did not create an incident in 3 polls"

    if backdate_seconds:
        incident.created_at -= _datetime.timedelta(seconds=backdate_seconds)

    return master_agent.handle_incident(incident)


def test_encoder_overload_resolves_on_first_action_no_escalation():
    fake_pipeline, fake_backup, detector, master_agent = build_stack()
    scenario = SCENARIOS["encoder_overload"]

    outcome = run_scenario_synchronously(scenario, fake_pipeline, detector, master_agent)

    assert outcome.final_state == AgentState.RESOLVED
    assert outcome.attempts == 1
    assert outcome.actions_tried == [scenario.expected_resolving_action]
    assert scenario.expected_escalation is False


def test_network_degradation_escalates_and_still_resolves():
    fake_pipeline, fake_backup, detector, master_agent = build_stack()
    scenario = SCENARIOS["network_degradation"]

    outcome = run_scenario_synchronously(scenario, fake_pipeline, detector, master_agent)

    assert outcome.final_state == AgentState.RESOLVED
    assert outcome.attempts > 1  # at least one escalation
    assert outcome.actions_tried[-1] == scenario.expected_resolving_action
    assert scenario.expected_escalation is True


def test_encoder_failure_resolves_and_reports_nonzero_rpo():
    with tempfile.TemporaryDirectory() as output_dir:
        # stream "started" well in the past and NOTHING is ever written
        # to this directory -- a genuine, measurable gap for the whole
        # incident window.
        stream_start = time.time() - 30.0
        accounting = OutputAccounting([output_dir], stream_start=stream_start)

        fake_pipeline, fake_backup, detector, master_agent = build_stack(accounting=accounting)
        scenario = SCENARIOS["encoder_failure"]

        outcome = run_scenario_synchronously(scenario, fake_pipeline, detector, master_agent, backdate_seconds=6.0)

        assert outcome.final_state == AgentState.RESOLVED
        assert outcome.rpo is not None
        assert outcome.rpo.segments_missing > 0
        assert outcome.rpo.seconds_affected > 0.0


def test_runner_reset_returns_to_healthy_baseline_from_any_state():
    from detector.models import IncidentType

    fake_pipeline, fake_backup, detector, master_agent = build_stack()

    # Push it into a faulted, non-primary, non-default state.
    from simulator.failures import encoder_overload
    with fake_pipeline.state_lock:
        fake_pipeline.current_state = encoder_overload()
        fake_pipeline.current_state.active_output = "backup"
        fake_pipeline.current_state.bitrate_factor = 0.3
    detector._breach_counts[IncidentType.ENCODER_OVERLOAD] = 2

    reset_all(fake_pipeline, fake_backup.reset, detector)

    with fake_pipeline.state_lock:
        state = fake_pipeline.current_state
        assert state.encoder_fault == "healthy"
        assert state.network_fault is False
        assert state.bitrate_factor == 1.0
        assert state.active_output == "primary"
    assert fake_backup.reset_calls == 1
    assert all(v == 0 for v in detector._breach_counts.values())
