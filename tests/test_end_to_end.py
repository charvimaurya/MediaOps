"""
End-to-end: a fake pipeline (no real FFmpeg, no Prometheus) wired through
the real MasterAgent, real GuardrailEngine, real RealActionExecutor, and
real PipelineControl -- proving the whole spine mechanically, not just
each piece in isolation.
"""

import threading
from datetime import datetime, timezone

from agent.master_agent import MasterAgent, AgentState
from agent.diagnosis import DiagnosisProvider, Diagnosis
from agent.knowledge import KnowledgeBase
from agent.executor import RealActionExecutor
from agent.verification import Verifier
from agent.guardrails import GuardrailEngine
from agent import policy
from simulator.control import PipelineControl
from simulator.failures import MediaState, encoder_overload, network_degradation
from detector.models import Incident, IncidentType, Severity, IncidentStatus


class FakePipeline:
    def __init__(self, state):
        self.current_state = state
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

    def start(self) -> bool:
        return True

    def restart(self) -> bool:
        return True


class FakeTelemetryClient:
    """Stands in for detector.prometheus.PrometheusClient -- reads
    directly off the fake pipeline's current state, so verification
    reflects real PipelineControl effects without needing Prometheus."""

    def __init__(self, fake_pipeline):
        self._pipeline = fake_pipeline

    def get_telemetry(self) -> dict:
        with self._pipeline.state_lock:
            state = self._pipeline.current_state
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
            }


class FixedRecommendationDiagnosisProvider(DiagnosisProvider):
    """Fake diagnosis -- always recommends the same (possibly wrong)
    action, so the test controls exactly what MasterAgent tries first."""

    def __init__(self, recommended_action: str, confidence: float = 0.9):
        self._recommended_action = recommended_action
        self._confidence = confidence

    def diagnose(self, incident, telemetry, history) -> Diagnosis:
        return Diagnosis(
            root_cause="fake", evidence=["fake evidence"], confidence=self._confidence,
            recommended_action=self._recommended_action,
        )


def make_incident(state, incident_type):
    return Incident(
        incident_id="INC-E2E",
        type=incident_type,
        severity=Severity.HIGH,
        reason="test",
        status=IncidentStatus.OPEN,
        created_at=datetime.now(timezone.utc),
        resolved_at=None,
        telemetry_snapshot={
            "fps": state.fps, "cpu_usage": state.cpu_usage, "encoding_latency": state.encoding_latency,
            "dropped_frames": state.dropped_frames, "packet_loss": state.packet_loss,
            "network_latency": state.network_latency, "encoder_status": state.encoder_status,
            "bitrate": state.bitrate,
        },
        breach_count=3,
    )


def build_agent(fake_pipeline, diagnosis_provider):
    control = PipelineControl(pipeline_handle=fake_pipeline, backup_instance=FakeBackup())
    return MasterAgent(
        diagnosis_provider=diagnosis_provider,
        knowledge_base=KnowledgeBase(),
        action_executor=RealActionExecutor(control=control),
        verifier=Verifier(FakeTelemetryClient(fake_pipeline), settle_seconds=0),
        guardrail_engine=GuardrailEngine(cooldown_seconds=0),
    )


def test_encoder_overload_resolves_via_restart_or_switch_backup():
    state = encoder_overload()
    fake_pipeline = FakePipeline(state)
    incident = make_incident(state, IncidentType.ENCODER_OVERLOAD)

    # StubDiagnosisProvider-equivalent: recommend the type's own first
    # allowed action, matching what the real stub would pick.
    diagnosis_provider = FixedRecommendationDiagnosisProvider(policy.allowed_actions(IncidentType.ENCODER_OVERLOAD)[0])
    agent = build_agent(fake_pipeline, diagnosis_provider)

    outcome = agent.handle_incident(incident)

    assert outcome.final_state == AgentState.RESOLVED
    winning_action = outcome.actions_tried[-1]
    assert winning_action in ("restart_encoder", "switch_backup")


def test_network_degradation_wrong_action_denied_then_escalates_to_resolution():
    state = network_degradation()
    fake_pipeline = FakePipeline(state)
    incident = make_incident(state, IncidentType.NETWORK_DEGRADATION)

    # restart_encoder is NOT in network_degradation's allowed_actions --
    # the guardrail must deny it before it ever executes. reduce_bitrate
    # at its default factor is tuned to provide only partial relief (see
    # simulator/failures.py), so it fails verification too, and the
    # agent has to escalate all the way to failover to actually resolve.
    diagnosis_provider = FixedRecommendationDiagnosisProvider("restart_encoder")
    agent = build_agent(fake_pipeline, diagnosis_provider)

    outcome = agent.handle_incident(incident)

    denial_events = [
        e for e in outcome.timeline
        if e["stage"] == AgentState.GUARDRAIL_CHECK.value and e.get("data", {}).get("denied_action") == "restart_encoder"
    ]
    assert len(denial_events) == 1
    assert "restart_encoder" not in outcome.actions_tried
    assert outcome.final_state == AgentState.RESOLVED
    assert "reduce_bitrate" in outcome.actions_tried  # tried, but didn't fully resolve it
    assert outcome.actions_tried[-1] == "failover"  # what actually resolved it
