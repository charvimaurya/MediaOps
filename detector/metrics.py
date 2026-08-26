"""
New Prometheus metrics for the incident detector (section G), registered
against the same global default registry simulator/telemetry.py already
uses -- there is no separate registry to invent.

Exposed via this component's own HTTP server on port 8002 (started in
detector/detector.py's __main__ entry point), since the detector runs as
its own process and can't share pipeline.py's existing :8000 server
(separate OS process, separate in-memory registry instance). Adding a
Prometheus scrape target for :8002 is a follow-up config change,
intentionally left out here since the brief says not to touch
prometheus/prometheus.yml beyond this metrics addition.
"""

from prometheus_client import Counter, Gauge

INCIDENTS_TOTAL = Counter(
    "mediaops_incidents_total",
    "Total incidents created by the incident detector",
    ["type"],
)

OPEN_INCIDENTS = Gauge(
    "mediaops_open_incidents",
    "Currently open incidents by type (0 or 1 -- only one incident per type is open at a time)",
    ["type"],
)
