"""Preview and optionally delete stale, non-terminal Firestore incidents.

This helper touches only the IncidentRecorder's configured incident collection;
it never opens the knowledge-base collection. Preview is the default.

    python3 -m tools.cleanup_incidents --older-than-hours 24
    python3 -m tools.cleanup_incidents --older-than-hours 24 --delete
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from pydantic import ValidationError

from incident_recorder import INCIDENTS_COLLECTION, IncidentRecorder
from models import TERMINAL_STATUSES, Incident, IncidentStatus


@dataclass(frozen=True)
class StaleIncident:
    incident_id: str
    status: IncidentStatus
    fault_class: str
    current_step: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ScanResult:
    candidates: list[StaleIncident]
    malformed: list[tuple[str, str]]


def scan_stale_incidents(
    recorder: IncidentRecorder,
    *,
    older_than: timedelta,
    now: datetime | None = None,
) -> ScanResult:
    """Return stale active incidents and malformed documents without mutating."""
    if older_than.total_seconds() < 0:
        raise ValueError("older_than must be non-negative")
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    cutoff = observed_at - older_than
    candidates: list[StaleIncident] = []
    malformed: list[tuple[str, str]] = []

    for snapshot in recorder._col.stream():
        try:
            data = snapshot.to_dict()
            if data is None:
                raise ValueError("document has no data")
            incident = Incident.model_validate(data)
            if incident.updated_at.tzinfo is None or incident.updated_at.utcoffset() is None:
                raise ValueError("updated_at is not timezone-aware")
        except (ValidationError, TypeError, ValueError) as exc:
            malformed.append((snapshot.id, f"{type(exc).__name__}: {exc}"))
            continue

        if incident.status in TERMINAL_STATUSES or incident.updated_at > cutoff:
            continue
        candidates.append(StaleIncident(
            incident_id=incident.incident_id,
            status=incident.status,
            fault_class=incident.anomaly.fault_class.value,
            current_step=incident.current_step,
            created_at=incident.created_at,
            updated_at=incident.updated_at,
        ))

    candidates.sort(key=lambda item: item.updated_at)
    malformed.sort(key=lambda item: item[0])
    return ScanResult(candidates, malformed)


def delete_candidates(
    recorder: IncidentRecorder,
    candidates: Iterable[StaleIncident],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Delete only documents whose status and timestamp still match the preview."""
    deleted: list[str] = []
    skipped: list[tuple[str, str]] = []
    for candidate in candidates:
        ref = recorder._col.document(candidate.incident_id)
        snapshot = ref.get()
        if not snapshot.exists:
            skipped.append((candidate.incident_id, "document no longer exists"))
            continue
        try:
            incident = Incident.model_validate(snapshot.to_dict())
        except (ValidationError, TypeError, ValueError) as exc:
            skipped.append((candidate.incident_id, f"document became malformed: {exc}"))
            continue
        if incident.status != candidate.status or incident.updated_at != candidate.updated_at:
            skipped.append((candidate.incident_id, "status or updated_at changed after preview"))
            continue
        if incident.status in TERMINAL_STATUSES:
            skipped.append((candidate.incident_id, "incident is now terminal"))
            continue
        ref.delete()
        deleted.append(candidate.incident_id)
    return deleted, skipped


def _age(now: datetime, timestamp: datetime) -> str:
    seconds = max(0, int((now - timestamp).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m"


def print_scan(result: ScanResult, *, now: datetime, collection_name: str) -> None:
    print(f"Firestore collection: {collection_name}")
    print(f"Stale active incidents matched: {len(result.candidates)}")
    for item in result.candidates:
        print(
            f"- {item.incident_id}  status={item.status.value}  "
            f"fault={item.fault_class}  step={item.current_step}  age={_age(now, item.updated_at)}\n"
            f"  created_at={item.created_at.isoformat()}  updated_at={item.updated_at.isoformat()}"
        )
    if result.malformed:
        print(f"Malformed documents excluded from deletion: {len(result.malformed)}")
        for document_id, reason in result.malformed:
            print(f"- {document_id}: {reason}")


def main(
    argv: list[str] | None = None,
    *,
    recorder_factory: Callable[[], IncidentRecorder] = IncidentRecorder,
    input_fn: Callable[[str], str] = input,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--older-than-hours",
        type=float,
        default=24.0,
        help="minimum age since updated_at (default: 24)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="delete displayed matches after exact interactive confirmation",
    )
    args = parser.parse_args(argv)
    if args.older_than_hours < 0:
        parser.error("--older-than-hours must be non-negative")

    recorder = recorder_factory()
    now = now or datetime.now(timezone.utc)
    result = scan_stale_incidents(
        recorder,
        older_than=timedelta(hours=args.older_than_hours),
        now=now,
    )
    print_scan(result, now=now, collection_name=recorder._col.id)

    if not args.delete:
        print("PREVIEW ONLY: nothing deleted. Add --delete to request deletion.")
        return 0
    if not result.candidates:
        print("Nothing to delete.")
        return 0

    expected = f"DELETE {len(result.candidates)}"
    answer = input_fn(f"Type {expected!r} to delete exactly these incidents: ")
    if answer != expected:
        print("Cancelled: nothing deleted.")
        return 1

    deleted, skipped = delete_candidates(recorder, result.candidates)
    for incident_id in deleted:
        print(f"DELETED {incident_id}")
    for incident_id, reason in skipped:
        print(f"SKIPPED {incident_id}: {reason}")
    print(f"Deleted {len(deleted)}; skipped {len(skipped)}.")
    return 0 if not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
