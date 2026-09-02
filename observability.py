"""Small shared helpers for consistent logs and durable incident breadcrumbs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from models import Incident, IncidentStatus, LifecycleEvent, RemediationAction


def log_event(
    component: str,
    incident_id: str,
    step: str,
    outcome: str,
    *,
    action: RemediationAction | str | None = None,
    detail: str = "",
    level: int = logging.INFO,
) -> None:
    """Emit one grep-friendly JSON log line with the same fields everywhere."""
    action_value = action.value if isinstance(action, RemediationAction) else action
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "component": component,
        "incident_id": incident_id,
        "step": step,
        "action": action_value,
        "outcome": outcome,
        "detail": detail,
    }
    logging.getLogger(component).log(level, json.dumps(payload, sort_keys=True))


def record_event(
    incident: Incident,
    component: str,
    step: str,
    outcome: str,
    *,
    status: IncidentStatus | None = None,
    action: RemediationAction | None = None,
    detail: str = "",
) -> LifecycleEvent:
    """Append a structured event and emit its matching process log."""
    event = LifecycleEvent(
        component=component,
        step=step,
        outcome=outcome,
        status=status,
        action=action,
        detail=detail,
    )
    incident.lifecycle_events.append(event)
    log_event(
        component,
        incident.incident_id,
        step,
        outcome,
        action=action,
        detail=detail,
    )
    return event
