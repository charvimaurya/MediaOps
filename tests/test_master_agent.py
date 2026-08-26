from datetime import datetime, timezone

from agent.master_agent import MasterAgent, AgentState
from agent.guardrails import GuardrailEngine
from agent.diagnosis import DiagnosisProvider, Diagnosis
from agent.remediation import ActionExecutor, ActionResult
from agent.verification import VerificationResult
from agent.knowledge import KnowledgeBase
from agent import policy
from detector.models import Incident, IncidentType, Severity, IncidentStatus


def make_incident(incident_type=IncidentType.ENCODER_OVERLOAD):
    return Incident(
        incident_id="INC-TEST",
        type=incident_type,
        severity=Severity.HIGH,
        reason="test incident",
        status=IncidentStatus.OPEN,
        created_at=datetime.now(timezone.utc),
        resolved_at=None,
        telemetry_snapshot={
            "cpu_usage": 97.0,
            "fps": 18.0,
            "encoding_latency": 190.0,
            "dropped_frames": 8.2,
        },
        breach_count=3,
    )


class FakeDiagnosisProvider(DiagnosisProvider):
    def __init__(self, recommended_action):
        self._recommended_action = recommended_action

    def diagnose(self, incident, telemetry, history):
        return Diagnosis(
            root_cause="fake root cause",
            evidence=["fake evidence"],
            confidence=0.8,
            recommended_action=self._recommended_action,
        )


class FakeActionExecutor(ActionExecutor):
    def __init__(self):
        self.executed_actions = []

    def execute(self, action, incident):
        self.executed_actions.append(action)
        now = datetime.now(timezone.utc)
        return ActionResult(action=action, started_at=now, completed_at=now, executed=True, detail="fake execution")


class FakeVerifier:
    """Duck-typed -- MasterAgent only needs .verify(). No sleeping, no
    real telemetry."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    def verify(self, settle_seconds=None):
        assert self._outcomes, "FakeVerifier ran out of scripted outcomes -- more verify() calls than expected"
        passed = self._outcomes.pop(0)
        return VerificationResult(passed=passed, telemetry={}, failed_checks=[] if passed else ["fake_failure"])

    @property
    def remaining(self):
        return list(self._outcomes)


class EmptyKnowledgeBase(KnowledgeBase):
    def __init__(self):
        self._records = []


def make_agent(recommended_action, verify_outcomes):
    diagnosis = FakeDiagnosisProvider(recommended_action)
    executor = FakeActionExecutor()
    verifier = FakeVerifier(verify_outcomes)
    kb = EmptyKnowledgeBase()
    agent = MasterAgent(diagnosis, kb, executor, verifier)
    return agent, executor, verifier


def test_verification_passes_first_try():
    incident = make_incident()
    agent, executor, verifier = make_agent(recommended_action="restart_encoder", verify_outcomes=[True])

    outcome = agent.handle_incident(incident)

    assert outcome.final_state == AgentState.RESOLVED
    assert outcome.attempts == 1
    assert outcome.rto_seconds is not None
    assert executor.executed_actions == ["restart_encoder"]
    assert verifier.remaining == []


def test_fails_once_then_passes():
    incident = make_incident()
    agent, executor, verifier = make_agent(recommended_action="restart_encoder", verify_outcomes=[False, True])

    outcome = agent.handle_incident(incident)

    assert outcome.final_state == AgentState.RESOLVED
    assert outcome.attempts == 2
    assert executor.executed_actions[0] != executor.executed_actions[1]
    assert verifier.remaining == []


def test_always_fails_reaches_failed_safe_executes_failover_exactly_once():
    incident = make_incident(IncidentType.ENCODER_OVERLOAD)
    # allowed = [restart_encoder, switch_backup]; expect exactly 3
    # real attempts: restart_encoder, switch_backup, then failover once
    # the escalation path reaches FAILSAFE.
    agent, executor, verifier = make_agent(recommended_action="restart_encoder", verify_outcomes=[False, False, False])

    outcome = agent.handle_incident(incident)

    assert outcome.final_state == AgentState.FAILED_SAFE
    assert executor.executed_actions == ["restart_encoder", "switch_backup", "failover"]
    assert executor.executed_actions.count("failover") == 1
    assert len(executor.executed_actions) == len(set(executor.executed_actions))  # no repeats
    assert verifier.remaining == []  # exactly 3 verify() calls, no more


def test_diagnosis_action_outside_policy_is_rejected_then_permitted_one_runs():
    incident = make_incident(IncidentType.ENCODER_OVERLOAD)
    agent, executor, verifier = make_agent(recommended_action="delete_everything", verify_outcomes=[True])

    outcome = agent.handle_incident(incident)

    denial_events = [
        e for e in outcome.timeline if e["stage"] == AgentState.GUARDRAIL_CHECK.value and "DENIED" in e["message"]
    ]
    assert len(denial_events) == 1
    assert "delete_everything" not in executor.executed_actions
    assert outcome.final_state == AgentState.RESOLVED
    assert executor.executed_actions == ["restart_encoder"]
    assert executor.executed_actions[0] in policy.allowed_actions(IncidentType.ENCODER_OVERLOAD)


def test_timeline_has_one_event_per_transition_in_order():
    incident = make_incident()
    agent, executor, verifier = make_agent(recommended_action="restart_encoder", verify_outcomes=[True])

    outcome = agent.handle_incident(incident)

    stages = [e["stage"] for e in outcome.timeline]
    assert stages == [
        "RECEIVED",
        "DIAGNOSING",
        "CONSULTING_KNOWLEDGE",
        "DECIDING",
        "GUARDRAIL_CHECK",
        "ACTING",
        "VERIFYING",
        "RESOLVED",
    ]


def test_second_incident_while_one_in_flight_is_logged_as_queued(caplog):
    import threading
    import time

    class SlowVerifier:
        def verify(self, settle_seconds=None):
            time.sleep(0.2)
            return VerificationResult(passed=True, telemetry={}, failed_checks=[])

    diagnosis = FakeDiagnosisProvider("restart_encoder")
    executor = FakeActionExecutor()
    kb = EmptyKnowledgeBase()
    # cooldown_seconds=0: this test is about the busy/queue behavior of
    # MasterAgent's own lock, not about the guardrail engine's (separate,
    # intentional) cross-incident cooldown -- both incidents use the same
    # action name, which would otherwise legitimately collide.
    agent = MasterAgent(diagnosis, kb, executor, SlowVerifier(), guardrail_engine=GuardrailEngine(cooldown_seconds=0))

    incident_a = make_incident()
    incident_b = make_incident()

    results = {}

    def run(name, incident):
        results[name] = agent.handle_incident(incident)

    with caplog.at_level("INFO"):
        t1 = threading.Thread(target=run, args=("a", incident_a))
        t1.start()
        time.sleep(0.05)  # let incident_a acquire the lock first
        t2 = threading.Thread(target=run, args=("b", incident_b))
        t2.start()
        t1.join()
        t2.join()

    assert results["a"].final_state == AgentState.RESOLVED
    assert results["b"].final_state == AgentState.RESOLVED
    assert any("busy -- queuing incident" in message for message in caplog.messages)
