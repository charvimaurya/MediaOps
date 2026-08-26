"""
Declarative scenario definitions for the demo runner. No logic here --
just data, matching this project's established "policy table, not a
rules engine" style (see agent/policy.py).

expected_resolving_action / expected_escalation were derived by hand-
tracing the actual policy/guardrail/fault-model behavior (see
simulator/failures.py's network-layer tuning comment and
agent/aht.py's CONFIG), then confirmed by the tests in
tests/test_output_accounting.py and the live scenario runs -- not
asserted first and worked backward from.
"""

from dataclasses import dataclass

from detector.models import IncidentType


@dataclass
class Scenario:
    name: str
    incident_type: IncidentType
    fault_factory: str  # name of the simulator.failures factory function
    expected_resolving_action: str
    expected_escalation: bool
    expected_max_aht_seconds: float


SCENARIOS = {
    "encoder_overload": Scenario(
        name="encoder_overload",
        incident_type=IncidentType.ENCODER_OVERLOAD,
        fault_factory="encoder_overload",
        expected_resolving_action="restart_encoder",
        expected_escalation=False,
        expected_max_aht_seconds=15.0,
    ),
    "network_degradation": Scenario(
        name="network_degradation",
        incident_type=IncidentType.NETWORK_DEGRADATION,
        fault_factory="network_degradation",
        # reduce_bitrate's default factor is tuned to be only partial
        # relief (see simulator/failures.py) -- verification fails, the
        # path escalates, and because network_fault is a shared
        # condition switch_backup can't fix either, so failover (which
        # also cuts bitrate further) is what actually resolves it.
        expected_resolving_action="failover",
        expected_escalation=True,
        expected_max_aht_seconds=20.0,
    ),
    "encoder_failure": Scenario(
        name="encoder_failure",
        incident_type=IncidentType.ENCODER_FAILURE,
        fault_factory="encoder_failure",
        expected_resolving_action="restart_encoder",
        expected_escalation=False,
        expected_max_aht_seconds=15.0,
    ),
}
