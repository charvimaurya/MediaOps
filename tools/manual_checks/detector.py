"""
Offline sanity check for detector.py -- drives the persistence/dedup state
machine directly by hand, no Prometheus, no threads, no sleeping for real time.

    python3 -m tools.manual_checks.detector

Covers: emit-once after the window, dedup while broken, reset on health=1,
re-emit for a fresh problem, and that a None reading neither emits nor resets.
"""

import time

from detector import Detector
from models import AnomalyEvent

emitted: list[AnomalyEvent] = []


def sink(event: AnomalyEvent) -> None:
    emitted.append(event)


def fresh() -> Detector:
    emitted.clear()
    # tiny window so a couple of hand-fed polls cross it; snapshot query will
    # just come back empty if Prometheus isn't running -- that's fine here.
    return Detector(sink, persistence_window_seconds=0.05)


print("1. Emits exactly one AnomalyEvent after the window, then dedups")
d = fresh()
d._handle_health(0)          # first breach -> window starts
assert emitted == []
time.sleep(0.03)
d._handle_health(0)          # still inside window
assert emitted == []
time.sleep(0.03)
d._handle_health(0)          # window crossed -> emit
d._handle_health(0)          # still broken -> must NOT emit again
d._handle_health(0)
assert len(emitted) == 1, f"expected 1 emit, got {len(emitted)}"
assert emitted[0].breach_count == 3, emitted[0].breach_count
assert emitted[0].health_value == 0
assert emitted[0].source == "prometheus"
print(f"   OK: 1 event, breach_count={emitted[0].breach_count}")
print(f"   reason: {emitted[0].reason}")

print("\n2. health=1 resets, and a fresh problem emits again")
d._handle_health(1)          # recovery observed -> reset
assert d._emitted is False and d._breach_started_at is None
time.sleep(0.06)
d._handle_health(0)          # new problem, new window (already past 0.05s? no -- new start)
time.sleep(0.06)
d._handle_health(0)          # window crossed -> second emit
assert len(emitted) == 2, f"expected 2 emits total, got {len(emitted)}"
print(f"   OK: reset worked, second event emitted (total={len(emitted)})")

print("\n3. A None reading neither emits nor resets")
d = fresh()
time.sleep(0.06)
d._handle_health(0)          # start window in the past
d._handle_health(None)       # unknown -- must not reset the window
time.sleep(0.06)
d._handle_health(0)          # window crossed -> emit (proves None didn't reset)
assert len(emitted) == 1, f"expected 1 emit, got {len(emitted)}"
# now prove None doesn't emit on its own and doesn't clear the dedup latch
d._handle_health(None)
assert len(emitted) == 1
print("   OK: None is a no-op for both the window and the dedup latch")

print("\n4. Never breaching -> never emits")
d = fresh()
for _ in range(5):
    d._handle_health(1)
assert emitted == []
print("   OK: healthy stream produces no events")

print("\nALL CHECKS PASSED")
