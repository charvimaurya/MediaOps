from datetime import datetime, timezone

from agent.guardrails import GuardrailEngine
from agent.diagnosis import Diagnosis
from agent import policy
from detector.models import Incident, IncidentType, Severity, IncidentStatus


def make_incident(incident_id="INC-1", incident_type=IncidentType.ENCODER_OVERLOAD):
    return Incident(
        incident_id=incident_id,
        type=incident_type,
        severity=Severity.HIGH,
        reason="test",
        status=IncidentStatus.OPEN,
        created_at=datetime.now(timezone.utc),
        resolved_at=None,
        telemetry_snapshot={},
        breach_count=3,
    )


def make_diagnosis(confidence=0.9, recommended_action="restart_encoder"):
    return Diagnosis(root_cause="test", evidence=["x"], confidence=confidence, recommended_action=recommended_action)


def test_action_outside_allowed_list_is_denied_with_rule_named():
    engine = GuardrailEngine()
    incident = make_incident()

    decision = engine.check("delete_everything", incident, make_diagnosis())

    assert decision.allowed is False
    assert decision.rule == "allowed_actions"


def test_same_action_within_cooldown_is_denied_then_permitted_after():
    engine = GuardrailEngine(cooldown_seconds=0.1)
    incident = make_incident()

    first = engine.check("restart_encoder", incident, make_diagnosis())
    assert first.allowed is True
    engine.release()

    second = engine.check("restart_encoder", incident, make_diagnosis())
    assert second.allowed is False
    assert second.rule == "cooldown"

    import time

    time.sleep(0.15)

    third = engine.check("restart_encoder", incident, make_diagnosis())
    assert third.allowed is True


def test_failover_permitted_despite_active_cooldown():
    engine = GuardrailEngine(cooldown_seconds=10.0)
    incident = make_incident()

    first = engine.check("failover", incident, make_diagnosis())
    assert first.allowed is True
    engine.release()

    # immediately again, well within the 10s cooldown window
    second = engine.check("failover", incident, make_diagnosis())
    assert second.allowed is True


def test_low_confidence_denies_restart_encoder_and_permits_reduce_bitrate():
    engine = GuardrailEngine()
    low_confidence = make_diagnosis(confidence=0.3, recommended_action="restart_encoder")

    # restart_encoder is destructive -- denied under low confidence even
    # though it's permitted for this incident type.
    overload_incident = make_incident("INC-1", IncidentType.ENCODER_OVERLOAD)
    denied = engine.check("restart_encoder", overload_incident, low_confidence)
    assert denied.allowed is False
    assert denied.rule == "confidence_floor"

    # reduce_bitrate is non-destructive -- still permitted under the same
    # low confidence, for a type where it's allowed.
    network_incident = make_incident("INC-2", IncidentType.NETWORK_DEGRADATION)
    allowed = engine.check("reduce_bitrate", network_incident, low_confidence)
    assert allowed.allowed is True


def test_low_confidence_denies_failover_too():
    engine = GuardrailEngine()
    incident = make_incident(incident_type=IncidentType.ENCODER_FAILURE)
    low_confidence = make_diagnosis(confidence=0.3, recommended_action="switch_backup")

    decision = engine.check("failover", incident, low_confidence)

    assert decision.allowed is False
    assert decision.rule == "confidence_floor"


def test_in_flight_lock_denies_a_second_concurrent_action():
    engine = GuardrailEngine()
    incident_a = make_incident("INC-A")
    incident_b = make_incident("INC-B")

    first = engine.check("restart_encoder", incident_a, make_diagnosis())
    assert first.allowed is True
    # deliberately not releasing yet

    second = engine.check("switch_backup", incident_b, make_diagnosis())
    assert second.allowed is False
    assert second.rule == "in_flight_lock"

    engine.release()
    third = engine.check("switch_backup", incident_b, make_diagnosis())
    assert third.allowed is True


def test_max_attempts_exhaustion_is_denied():
    engine = GuardrailEngine(cooldown_seconds=0)
    incident = make_incident()

    actions = ["restart_encoder", "switch_backup", "restart_encoder"]
    for action in actions[: policy.MAX_ATTEMPTS]:
        decision = engine.check(action, incident, make_diagnosis())
        assert decision.allowed is True
        engine.release()

    decision = engine.check("switch_backup", incident, make_diagnosis())
    assert decision.allowed is False
    assert decision.rule == "max_attempts"


def test_max_attempts_does_not_block_a_different_incident():
    engine = GuardrailEngine(cooldown_seconds=0)
    incident_a = make_incident("INC-A")
    incident_b = make_incident("INC-B")

    for _ in range(policy.MAX_ATTEMPTS):
        decision = engine.check("restart_encoder", incident_a, make_diagnosis())
        assert decision.allowed is True
        engine.release()

    denied = engine.check("switch_backup", incident_a, make_diagnosis())
    assert denied.allowed is False

    allowed = engine.check("restart_encoder", incident_b, make_diagnosis())
    assert allowed.allowed is True


def test_prohibited_action_is_denied():
    from agent import guardrails

    engine = GuardrailEngine()
    incident = make_incident()

    guardrails.PROHIBITED_ACTIONS.append("restart_encoder")
    try:
        decision = engine.check("restart_encoder", incident, make_diagnosis())
        assert decision.allowed is False
        assert decision.rule == "prohibited_action"
    finally:
        guardrails.PROHIBITED_ACTIONS.remove("restart_encoder")
