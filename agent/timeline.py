"""
AgentEvent record + per-incident timeline. Every state transition and
capability call appends one of these -- this is the source of truth a
future dashboard/incident report renders from, rather than reconstructing
it from logs.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class AgentEvent:
    timestamp: datetime
    incident_id: str
    stage: str
    message: str
    data: dict
    aht_at_event: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "incident_id": self.incident_id,
            "stage": self.stage,
            "message": self.message,
            "data": self.data,
            "aht_at_event": self.aht_at_event,
        }


class Timeline:
    def __init__(self, incident_id: str):
        self._incident_id = incident_id
        self._events: List[AgentEvent] = []

    def record(self, stage: str, message: str, aht_at_event: float, data: Optional[dict] = None) -> AgentEvent:
        event = AgentEvent(
            timestamp=datetime.now(timezone.utc),
            incident_id=self._incident_id,
            stage=stage,
            message=message,
            data=data or {},
            aht_at_event=aht_at_event,
        )
        self._events.append(event)
        return event

    @property
    def events(self) -> List[AgentEvent]:
        return list(self._events)

    def to_list(self) -> List[dict]:
        return [e.to_dict() for e in self._events]
