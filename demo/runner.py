"""
Scenario runner CLI -- makes the three demo scenarios repeatable on
demand: reset, inject, watch, print, assert.

Core logic (reset_all, run_scenario_once) is decoupled from real
FFmpeg/Prometheus via injected handles, so tests/test_scenarios.py can
exercise it with fakes. The CLI entry point (main()) wires up the real
stack.

Usage:
    python3 -m demo.runner run <scenario>
    python3 -m demo.runner run all
    python3 -m demo.runner reset
"""

import argparse
import logging
import sys
import threading
import time

from simulator.failures import healthy_state
from agent.master_agent import MasterAgent, AgentState
from demo.scenarios import SCENARIOS

logger = logging.getLogger("demo.runner")

DETECTION_TIMEOUT_SECONDS = 30.0


# ============================================================
# CORE LOGIC -- testable against fakes
# ============================================================

def reset_all(pipeline_handle, reset_backup_fn, detector) -> None:
    """Thorough reset: clear faults, restore bitrate, switch back to
    primary, force_clear the detector. A stale bitrate or a still-active
    backup from the previous run is the most likely way a live demo
    fails, so healthy_state() resets every layered-fault field at once."""
    print("Resetting...")
    with pipeline_handle.state_lock:
        pipeline_handle.current_state = healthy_state()
    reset_backup_fn()
    detector.force_clear()
    print("Reset complete -- healthy baseline restored.")


def print_timeline(outcome) -> None:
    for event in outcome.timeline:
        print(f"  {event['stage']:<18} t+{event['aht_at_event']:>6.2f}s  {event['message']}")


def print_outcome_summary(outcome) -> None:
    print()
    print(f"  final_state:  {outcome.final_state.value}")
    print(f"  attempts:     {outcome.attempts}")
    print(f"  actions:      {outcome.actions_tried}")
    print(f"  AHT:          {outcome.aht_seconds:.2f}s")
    print(f"  RTO:          {outcome.rto_seconds:.2f}s" if outcome.rto_seconds is not None else "  RTO:          n/a (not resolved)")
    if outcome.rpo is not None:
        print(f"  RPO:          {outcome.rpo.seconds_affected:.1f}s ({outcome.rpo.segments_missing} segments missing)")
        print(f"                {outcome.rpo.method}")
    else:
        print("  RPO:          n/a (no output accounting attached)")


def check_expectations(outcome, scenario) -> bool:
    ok = True
    if outcome.final_state != AgentState.RESOLVED:
        print(f"  [FAIL] expected RESOLVED, got {outcome.final_state.value}")
        ok = False

    winning_action = outcome.actions_tried[-1] if outcome.actions_tried else None
    if winning_action != scenario.expected_resolving_action:
        print(f"  [FAIL] expected resolving action {scenario.expected_resolving_action!r}, got {winning_action!r}")
        ok = False

    escalated = len(outcome.actions_tried) > 1
    if escalated != scenario.expected_escalation:
        print(f"  [FAIL] expected escalation={scenario.expected_escalation}, got {escalated}")
        ok = False

    if outcome.aht_seconds > scenario.expected_max_aht_seconds:
        print(f"  [FAIL] AHT {outcome.aht_seconds:.2f}s exceeded expected max {scenario.expected_max_aht_seconds}s")
        ok = False

    print(f"  [{'PASS' if ok else 'FAIL'}] outcome matched expectations" if ok else "")
    return ok


def run_scenario_once(scenario, pipeline_handle, detector, master_agent, incident_wait_fn=None) -> object:
    """Injects the scenario's fault, waits for the detector+agent to
    produce an outcome, prints the timeline, and returns the outcome (or
    None on timeout). incident_wait_fn, if given, replaces the default
    polling wait (used by tests to avoid real sleeping)."""
    from simulator import failures as failures_module

    print(f"\n{'=' * 64}\nSCENARIO: {scenario.name}\n{'=' * 64}")

    outcome_holder = {}
    done = threading.Event()

    def on_outcome(outcome):
        outcome_holder["outcome"] = outcome
        done.set()

    factory = getattr(failures_module, scenario.fault_factory)
    with pipeline_handle.state_lock:
        pipeline_handle.current_state = factory()
    print(f"Injected fault: {scenario.fault_factory}")

    if incident_wait_fn is not None:
        outcome = incident_wait_fn(master_agent, done, outcome_holder)
    else:
        done.wait(DETECTION_TIMEOUT_SECONDS)
        outcome = outcome_holder.get("outcome")

    if outcome is None:
        print("  [FAIL] no outcome within timeout -- incident never detected/resolved")
        return None

    print_timeline(outcome)
    print_outcome_summary(outcome)
    check_expectations(outcome, scenario)
    return outcome


# ============================================================
# CLI -- wires up the real stack
# ============================================================

def _build_real_stack():
    from prometheus_client import start_http_server
    from simulator import pipeline, backup as backup_module
    from simulator.control import PipelineControl
    from simulator.output_accounting import OutputAccounting
    from detector.prometheus import PrometheusClient
    from detector.detector import IncidentDetector
    from agent.diagnosis import StubDiagnosisProvider
    from agent.knowledge import KnowledgeBase
    from agent.executor import RealActionExecutor
    from agent.verification import Verifier

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("Starting primary pipeline (ffmpeg + telemetry)...")
    start_http_server(pipeline.PROMETHEUS_PORT)
    pipeline.start_ffmpeg()
    threading.Thread(target=pipeline.telemetry_loop, daemon=True).start()

    backup_instance = backup_module.get_backup()
    print("Starting standby encoder...")
    backup_instance.start()

    accounting = OutputAccounting([pipeline.OUTPUT_DIR, backup_instance.output_dir])

    prom_client = PrometheusClient()
    control = PipelineControl(backup_instance=backup_instance)
    master_agent = MasterAgent(
        diagnosis_provider=StubDiagnosisProvider(),
        knowledge_base=KnowledgeBase(),
        action_executor=RealActionExecutor(control=control),
        verifier=Verifier(prom_client),
        output_accounting=accounting,
    )

    outcomes = []
    done_events = []

    def on_incident(incident):
        print(f"\n>>> Incident detected: {incident.incident_id} ({incident.type.value})")
        outcome = master_agent.handle_incident(incident)
        outcomes.append(outcome)
        for e in done_events:
            e.set()

    detector = IncidentDetector(prom_client, on_incident)
    detector.start()

    return pipeline, backup_module.reset_backup, detector, master_agent, outcomes


def _wait_for_healthy_baseline(seconds: float = 8.0) -> None:
    print(f"Waiting {seconds:.0f}s for a healthy telemetry baseline...")
    time.sleep(seconds)


def cmd_run(args) -> int:
    pipeline, reset_backup_fn, detector, master_agent, outcomes = _build_real_stack()
    reset_all(pipeline, reset_backup_fn, detector)
    _wait_for_healthy_baseline()

    names = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]
    if args.scenario != "all" and args.scenario not in SCENARIOS:
        print(f"Unknown scenario {args.scenario!r}. Known: {list(SCENARIOS.keys())}")
        return 2

    all_ok = True
    for name in names:
        scenario = SCENARIOS[name]

        outcomes.clear()
        outcome = run_scenario_once(scenario, pipeline, detector, master_agent)
        if outcome is None or outcome.final_state != AgentState.RESOLVED:
            all_ok = False

        reset_all(pipeline, reset_backup_fn, detector)
        if name != names[-1]:
            _wait_for_healthy_baseline()

    return 0 if all_ok else 1


def cmd_reset(args) -> int:
    pipeline, reset_backup_fn, detector, master_agent, outcomes = _build_real_stack()
    reset_all(pipeline, reset_backup_fn, detector)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="demo.runner")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="run one scenario or 'all'")
    run_parser.add_argument("scenario", choices=list(SCENARIOS.keys()) + ["all"])
    run_parser.set_defaults(func=cmd_run)

    reset_parser = sub.add_parser("reset", help="reset to a healthy baseline")
    reset_parser.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
