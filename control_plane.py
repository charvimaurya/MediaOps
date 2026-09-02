"""Deterministic, ALLOW-only Control Plane with Firestore idempotency.

This is the only component that invokes the simulator's real control methods.
An execution result reports only whether the method call succeeded; recovery is
owned by the later verification step.

    python3 control_plane.py <incident_id>
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from google.cloud import firestore

from incident_recorder import IncidentRecorder
from observability import log_event
from models import (
    ExecutionResult,
    Incident,
    IncidentStatus,
    RemediationAction,
    SafetyVerdict,
)
CONTROL_EXECUTIONS_COLLECTION = os.environ.get(
    "FIRESTORE_CONTROL_EXECUTIONS_COLLECTION", "control_executions"
)
SIMULATOR_CONTROL_URL = os.environ.get(
    "SIMULATOR_CONTROL_URL", "http://localhost:8001"
).rstrip("/")
SIMULATOR_CONTROL_TIMEOUT_SECONDS = float(
    os.environ.get("SIMULATOR_CONTROL_TIMEOUT_SECONDS", "5")
)

_CONTROL_PATHS = {
    RemediationAction.RESTART_ENCODER: "/control/restart-encoder",
    RemediationAction.REDUCE_PROFILE: "/control/reduce-profile",
    RemediationAction.SWITCH_SOURCE: "/control/switch-source",
    RemediationAction.FAILOVER: "/control/failover",
}


class ControlPlaneBlocked(RuntimeError):
    """The incident lacks valid deterministic authorization."""


class ExecutionInProgress(RuntimeError):
    """The key was claimed already, so retrying could duplicate execution."""


class ExecutionPersistenceError(RuntimeError):
    """Execution may have occurred, but its final result was not persisted."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ledger_document_id(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def _authorized_action(incident: Incident) -> tuple[RemediationAction, str]:
    """Return the gate-approved action/key or raise before any execution."""
    decision = incident.safety_decision
    if decision is None:
        raise ControlPlaneBlocked("SafetyDecision is missing")
    if decision.verdict is not SafetyVerdict.ALLOW:
        raise ControlPlaneBlocked(f"SafetyDecision verdict is {decision.verdict.value}, not ALLOW")
    if not decision.checks or any(not check.passed for check in decision.checks):
        raise ControlPlaneBlocked("SafetyDecision ALLOW does not contain all passing checks")
    if decision.block_reason is not None:
        raise ControlPlaneBlocked("SafetyDecision ALLOW unexpectedly has a block reason")
    if decision.incident_id != incident.incident_id:
        raise ControlPlaneBlocked("SafetyDecision incident_id does not match the incident")
    if incident.proposal is None:
        raise ControlPlaneBlocked("RemediationProposal is missing")
    if incident.proposal.incident_id != incident.incident_id:
        raise ControlPlaneBlocked("RemediationProposal incident_id does not match the incident")
    if decision.action is not incident.proposal.action:
        raise ControlPlaneBlocked("SafetyDecision action does not match the proposal")
    if incident.idempotency_key != decision.idempotency_key:
        raise ControlPlaneBlocked("incident idempotency_key does not match SafetyDecision")
    return decision.action, decision.idempotency_key


class SimulatorControlClient:
    """HTTP adapter to the four fixed actions in the simulator-owning process."""

    def __init__(
        self,
        base_url: str = SIMULATOR_CONTROL_URL,
        timeout_seconds: float = SIMULATOR_CONTROL_TIMEOUT_SECONDS,
        opener=urllib.request.urlopen,
    ) -> None:
        if not base_url or timeout_seconds <= 0:
            raise ValueError("simulator control URL and positive timeout are required")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._opener = opener

    def _post(self, action: RemediationAction) -> tuple[bool, str]:
        request = urllib.request.Request(
            self._base_url + _CONTROL_PATHS[action], method="POST"
        )
        with self._opener(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"action", "ok", "detail"}:
            raise ValueError("simulator returned an invalid control response shape")
        if payload["action"] != action.value:
            raise ValueError(
                f"simulator response action {payload['action']!r} does not match {action.value}"
            )
        if not isinstance(payload["ok"], bool):
            raise ValueError("simulator response ok must be a boolean")
        if not isinstance(payload["detail"], str) or not payload["detail"]:
            raise ValueError("simulator response detail must be a non-empty string")
        return payload["ok"], payload["detail"]

    def restart_encoder(self) -> tuple[bool, str]:
        return self._post(RemediationAction.RESTART_ENCODER)

    def reduce_bitrate(self, factor: float) -> tuple[bool, str]:
        if factor != 0.5:
            raise ValueError("REDUCE_PROFILE is fixed at bitrate factor 0.5")
        return self._post(RemediationAction.REDUCE_PROFILE)

    def switch_backup(self) -> tuple[bool, str]:
        return self._post(RemediationAction.SWITCH_SOURCE)

    def failover(self) -> tuple[bool, str]:
        return self._post(RemediationAction.FAILOVER)


def _make_controller() -> SimulatorControlClient:
    return SimulatorControlClient()


def _invoke(controller: Any, action: RemediationAction) -> tuple[bool, str]:
    """Fixed closed mapping from the approved enum to the real control surface."""
    dispatch: dict[RemediationAction, Callable[[], tuple[bool, str]]] = {
        RemediationAction.RESTART_ENCODER: controller.restart_encoder,
        RemediationAction.REDUCE_PROFILE: lambda: controller.reduce_bitrate(0.5),
        RemediationAction.SWITCH_SOURCE: controller.switch_backup,
        RemediationAction.FAILOVER: controller.failover,
    }
    method = dispatch.get(action)
    if method is None:
        raise ControlPlaneBlocked(f"no control method mapped for action {action!r}")
    return method()


def execute_incident(
    incident_id: str,
    *,
    recorder: IncidentRecorder | None = None,
    controller: Any | None = None,
) -> ExecutionResult:
    """Atomically claim, execute once, and persist the control-call result."""
    active_recorder = recorder or IncidentRecorder()
    log_event("control_plane", incident_id, "execute", "started")
    db = active_recorder._db
    incident_ref = active_recorder._col.document(incident_id)
    ledger_col = db.collection(CONTROL_EXECUTIONS_COLLECTION)
    claim_token = uuid.uuid4().hex

    transaction = db.transaction()

    @firestore.transactional
    def claim(txn):
        incident_snapshot = incident_ref.get(transaction=txn)
        if not incident_snapshot.exists:
            raise KeyError(f"no incident {incident_id} in Firestore")
        incident = Incident.model_validate(incident_snapshot.to_dict())
        action, key = _authorized_action(incident)
        ledger_ref = ledger_col.document(_ledger_document_id(key))
        ledger_snapshot = ledger_ref.get(transaction=txn)
        if ledger_snapshot.exists:
            ledger = ledger_snapshot.to_dict() or {}
            if ledger.get("idempotency_key") != key:
                raise ControlPlaneBlocked("idempotency ledger key collision")
            if ledger.get("state") == "COMPLETED":
                return "completed", ExecutionResult.model_validate(ledger.get("result"))
            raise ExecutionInProgress(
                f"idempotency key {key!r} is already claimed; refusing duplicate execution"
            )

        claimed_at = _utcnow()
        txn.set(ledger_ref, {
            "state": "IN_PROGRESS",
            "idempotency_key": key,
            "incident_id": incident_id,
            "action": action.value,
            "claim_token": claim_token,
            "claimed_at": claimed_at,
        })
        txn.update(incident_ref, {
            "status": IncidentStatus.EXECUTING.value,
            "current_step": "execute",
            "updated_at": claimed_at,
        })
        return "claimed", (action, key, ledger_ref)

    state, payload = claim(transaction)
    if state == "completed":
        log_event("control_plane", incident_id, "execute", "idempotent_noop",
                  action=payload.action, detail="returned prior execution result")
        return payload

    action, key, ledger_ref = payload
    active_controller = controller or _make_controller()
    try:
        success, detail = _invoke(active_controller, action)
        if not isinstance(success, bool) or not isinstance(detail, str) or not detail:
            raise TypeError("control method must return tuple[bool, non-empty str]")
    except Exception as exc:  # execution failure is recorded, never presented as success
        success = False
        detail = f"{type(exc).__name__}: {exc}"

    result = ExecutionResult(
        incident_id=incident_id,
        action=action,
        idempotency_key=key,
        success=success,
        detail=detail,
    )

    finalize_transaction = db.transaction()

    @firestore.transactional
    def finalize(txn):
        ledger_snapshot = ledger_ref.get(transaction=txn)
        incident_snapshot = incident_ref.get(transaction=txn)
        if not ledger_snapshot.exists or not incident_snapshot.exists:
            raise ExecutionPersistenceError("claim or incident disappeared before finalization")
        ledger = ledger_snapshot.to_dict() or {}
        if ledger.get("state") != "IN_PROGRESS" or ledger.get("claim_token") != claim_token:
            raise ExecutionPersistenceError("execution claim changed before finalization")

        incident = Incident.model_validate(incident_snapshot.to_dict())
        incident.execution = result
        incident.status = IncidentStatus.EXECUTING
        incident.current_step = "execute"
        incident.actions_attempted.append(action)
        incident.attempt_count += 1
        incident.updated_at = _utcnow()
        incident.notes.append(
            f"control execution {action.value}: success={success}; {detail}"
        )
        txn.set(incident_ref, incident.model_dump(mode="json"))
        txn.set(ledger_ref, {
            **ledger,
            "state": "COMPLETED",
            "completed_at": result.executed_at,
            "result": result.model_dump(mode="json"),
        })

    try:
        finalize(finalize_transaction)
    except Exception as exc:
        raise ExecutionPersistenceError(
            "control call completed but result finalization failed; "
            "the IN_PROGRESS claim prevents duplicate execution"
        ) from exc
    log_event("control_plane", incident_id, "execute", "succeeded" if result.success else "failed",
              action=result.action, detail=result.detail)
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 control_plane.py <incident_id>")
    try:
        execution = execute_incident(sys.argv[1])
    except (KeyError, ControlPlaneBlocked, ExecutionInProgress, ExecutionPersistenceError) as exc:
        sys.exit(f"STOP (control plane): {exc}")
    print(execution.model_dump_json(indent=2))
    if not execution.success:
        sys.exit(1)
