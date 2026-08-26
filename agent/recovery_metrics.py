"""
RTO and RPO computation from real Incident timestamps and real output
accounting -- measured, never asserted. compute_rpo() returns None (not
a fabricated number) when accounting is unavailable or the window is
degenerate. An honest absent RPO beats an invented one.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from detector.models import Incident
from simulator.output_accounting import OutputAccounting, SEGMENT_DURATION_SECONDS


@dataclass
class RPOResult:
    segments_missing: int
    seconds_affected: float
    window_start: str
    window_end: str
    method: str

    def to_dict(self) -> dict:
        return {
            "segments_missing": self.segments_missing,
            "seconds_affected": self.seconds_affected,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "method": self.method,
        }


def compute_rto(incident: Incident) -> Optional[float]:
    """Seconds from created_at to resolved_at. None if the incident
    hasn't resolved (e.g. FAILED_SAFE) -- RTO isn't defined for that."""
    if incident.resolved_at is None:
        return None
    return (incident.resolved_at - incident.created_at).total_seconds()


def compute_rpo(incident: Incident, accounting: Optional[OutputAccounting]) -> Optional[RPOResult]:
    """Window runs from incident.created_at to resolved_at, or to now if
    the incident ended in FAILED_SAFE (resolved_at still None)."""

    if accounting is None:
        return None

    window_start_dt = incident.created_at
    window_end_dt = incident.resolved_at if incident.resolved_at is not None else datetime.now(timezone.utc)

    if window_end_dt <= window_start_dt:
        return None  # degenerate window

    gap = accounting.gap_between(window_start_dt.timestamp(), window_end_dt.timestamp())
    seconds_affected = gap.missing * SEGMENT_DURATION_SECONDS

    method = (
        f"Counted real HLS segment files present on disk between "
        f"{window_start_dt.isoformat()} and {window_end_dt.isoformat()}: "
        f"expected {gap.expected} segments at {SEGMENT_DURATION_SECONDS:.1f}s cadence, "
        f"found {gap.written} actually written, {gap.missing} missing "
        f"({seconds_affected:.1f}s of output affected)."
    )

    return RPOResult(
        segments_missing=gap.missing,
        seconds_affected=seconds_affected,
        window_start=window_start_dt.isoformat(),
        window_end=window_end_dt.isoformat(),
        method=method,
    )
