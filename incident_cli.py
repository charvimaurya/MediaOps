"""
Thin Firestore glue for running one pipeline step at a time from the terminal.

Each component's `__main__` calls `run_step()` with a `compute` callback. This
module owns ONLY the load / prerequisite-check / write-back plumbing -- no
component logic lives here. It reuses `incident_recorder.IncidentRecorder` as the
Firestore gateway (its `.load()` / `.save()` already do the durable read-back).

    if argv and looks_like_incident_id(argv[0]):
        run_step(argv[0], step_name=..., status=..., requires=(...), produces=...,
                 compute=lambda inc: <the component's own function>)
    else:
        <the component's existing standalone/demo behaviour, unchanged>
"""

from __future__ import annotations

import re
import sys
from typing import Any, Callable, Iterable, Optional

from incident_recorder import IncidentRecorder
from models import Incident, IncidentStatus

# uuid4().hex -- exactly 32 lowercase hex chars. Fault names ("overload") and
# demo kinds ("failure", "rgb", "network") never match, so a single positional
# arg is unambiguous.
_INCIDENT_ID = re.compile(r"[0-9a-f]{32}\Z")


def looks_like_incident_id(s: Optional[str]) -> bool:
    return bool(s and _INCIDENT_ID.match(s))


# Human-readable "run the earlier step first" hints, keyed by the missing field.
_EARLIER_STEP = {
    "vision": "python3 vision_agent.py <id> <section>",
    "infra": "python3 infra_agent.py <id>",
    "evidence": "python3 aggregator.py <id>",
    "precedent": "python3 knowledge_base.py <id>",
}


def load_incident(incident_id: str) -> tuple[IncidentRecorder, Incident]:
    """Load one incident or exit with a clear message."""
    rec = IncidentRecorder()
    try:
        return rec, rec.load(incident_id)
    except KeyError:
        sys.exit(f"ERROR: no incident {incident_id!r} in Firestore "
                 f"(collection {rec._col.id!r}). Run the Detector first.")


def run_step(
    incident_id: str,
    *,
    step_name: str,
    status: IncidentStatus,
    produces: str,
    compute: Callable[[Incident], Any],
    requires: Iterable[str] = (),
    render: Optional[Callable[[Any], str]] = None,
) -> Any:
    """
    Load the incident, check `requires` fields are populated, run `compute(inc)`,
    write its result into `inc.<produces>`, advance `status` / `current_step`, and
    save. On any exception or a None result: append a STOP note, save, exit(1).
    Returns the result on success.
    """
    rec, inc = load_incident(incident_id)

    missing = [f for f in requires if not getattr(inc, f, None)]
    if missing:
        hints = "  ".join(_EARLIER_STEP.get(f, f) for f in missing)
        sys.exit(f"ERROR: incident {incident_id} has no {', '.join(missing)} yet.\n"
                 f"       run first:  {hints}")

    print(f">>> {step_name}: incident {incident_id}  "
          f"(status {inc.status.value} -> {status.value})")

    try:
        result = compute(inc)
    except Exception as exc:  # infra failure, aggregation conflict, KB empty, ...
        inc.notes.append(f"{step_name} STOP: {type(exc).__name__}: {exc}")
        rec.save(inc)
        sys.exit(f"STOP ({step_name}): {exc}\n(reason written to incidents/{incident_id}.notes)")

    if result is None:
        inc.notes.append(f"{step_name} STOP: no result (structured output failed validation twice)")
        rec.save(inc)
        sys.exit(f"STOP ({step_name}): the agent produced no valid result "
                 f"(written to incidents/{incident_id}.notes)")

    setattr(inc, produces, result)
    inc.status = status
    inc.current_step = step_name
    rec.save(inc)

    print()
    print(render(result) if render else result.model_dump_json(indent=2))
    print(f"\n>>> wrote incidents/{incident_id}.{produces}  (status now {status.value})")
    return result
