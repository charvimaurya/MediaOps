"""
Simple historical-incident lookup, loaded from data/incident_history.json.
No vector search, no embeddings, no external service -- just a
type-keyed list, matching by exact incident type string.
"""

import json
from pathlib import Path
from typing import List, Optional, Union

from detector.models import IncidentType

DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "incident_history.json"


class KnowledgeBase:
    def __init__(self, path: Path = DEFAULT_HISTORY_PATH):
        self._records = self._load(path)

    @staticmethod
    def _load(path: Path) -> List[dict]:
        if not path.exists():
            return []
        with open(path, "r") as f:
            return json.load(f)

    @staticmethod
    def _type_value(incident_type: Union[IncidentType, str]) -> str:
        return incident_type.value if isinstance(incident_type, IncidentType) else incident_type

    def lookup(self, incident_type: Union[IncidentType, str]) -> List[dict]:
        type_value = self._type_value(incident_type)
        return [r for r in self._records if r["type"] == type_value]

    def most_successful_action(self, incident_type: Union[IncidentType, str]) -> Optional[str]:
        successes = [r for r in self.lookup(incident_type) if r.get("succeeded")]
        if not successes:
            return None
        best = min(successes, key=lambda r: r.get("recovery_seconds", float("inf")))
        return best["action_taken"]
