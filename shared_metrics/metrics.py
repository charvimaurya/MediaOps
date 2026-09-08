"""
Prometheus metrics shared by simulator control and output-accounting code.

They use the project's global default registry so the existing metrics HTTP
server exposes them without any additional wiring.
"""

from prometheus_client import Counter, Gauge, Histogram

AHT_SECONDS = Histogram(
    "mediaops_aht_seconds",
    "Automated Handling Time per incident, in seconds",
    ["type"],
)

RTO_SECONDS = Histogram(
    "mediaops_rto_seconds",
    "Recovery Time Objective (time to resolution) per incident, in seconds",
    ["type"],
)

AGENT_ACTIONS_TOTAL = Counter(
    "mediaops_agent_actions_total",
    "Remediation actions attempted by the Master Agent",
    ["action", "outcome"],
)

ESCALATION_PATH = Gauge(
    "mediaops_escalation_path",
    "Most recent escalation path decided for an in-flight incident (1 = active)",
    ["path"],
)

ACTION_DURATION_SECONDS = Histogram(
    "mediaops_action_duration_seconds",
    "Wall-clock time RealActionExecutor spent executing an action",
    ["action"],
)

GUARDRAIL_DENIALS_TOTAL = Counter(
    "mediaops_guardrail_denials_total",
    "Guardrail denials, by rule and action",
    ["rule", "action"],
)

ACTIVE_ENCODER = Gauge(
    "mediaops_active_encoder",
    "Which encoder instance is currently active: 0 = primary, 1 = backup",
)

SEGMENTS_EXPECTED_TOTAL = Counter(
    "mediaops_segments_expected_total",
    "Segments expected at the target cadence since the stream started",
)

SEGMENTS_WRITTEN_TOTAL = Counter(
    "mediaops_segments_written_total",
    "Segments actually found written to disk (real files, non-zero size)",
)

RPO_SECONDS = Histogram(
    "mediaops_rpo_seconds",
    "Recovery Point Objective (output-affected duration) per incident, in seconds",
    ["type"],
)
