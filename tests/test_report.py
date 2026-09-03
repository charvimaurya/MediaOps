from __future__ import annotations

import unittest
from unittest.mock import patch

import report
from models import VerificationResult, VerificationVerdict
from tests.test_safety_gate import make_incident


class Recorder:
    def __init__(self, incident):
        self.incident = incident

    def load(self, incident_id):
        return self.incident.model_copy(deep=True)

    def save(self, incident):
        self.incident = incident.model_copy(deep=True)


class ReportTests(unittest.TestCase):
    def test_slack_sender_uses_certifi_verified_tls_context(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

        context = object()
        with patch.object(
            report.ssl, "create_default_context", return_value=context
        ) as make_context, patch.object(
            report.urllib.request, "urlopen", return_value=Response()
        ) as open_url:
            report._send_slack("https://hooks.slack.com/services/test", "hello")

        make_context.assert_called_once_with(cafile=report.certifi.where())
        self.assertIs(open_url.call_args.kwargs["context"], context)

    def recovered_incident(self):
        incident = make_incident()
        incident.verification = VerificationResult(
            incident_id=incident.incident_id,
            action=incident.proposal.action,
            verdict=VerificationVerdict.RECOVERED,
            recovered=True,
            telemetry_ok=True,
            video_ok=True,
            health_value=1,
            stable_window_seconds=15,
        )
        return incident

    def test_sends_exactly_one_factual_message(self):
        incident = self.recovered_incident()
        recorder = Recorder(incident)
        sent = []

        def claim(_, __):
            claimed = recorder.load(incident.incident_id)
            claimed.report_claimed = True
            recorder.save(claimed)
            return claimed

        with patch.object(report, "_claim_once", side_effect=claim):
            result = report.run_report(
                incident.incident_id,
                recorder=recorder,
                webhook_url="https://example.invalid/hook",
                sender=lambda url, text: sent.append((url, text)),
            )
        self.assertTrue(result["sent"])
        self.assertEqual(len(sent), 1)
        self.assertIn("Final verdict: RECOVERED", sent[0][1])
        self.assertTrue(recorder.incident.report_sent)

    def test_duplicate_claim_stops_before_send(self):
        incident = self.recovered_incident()
        recorder = Recorder(incident)
        with patch.object(
            report, "_claim_once", side_effect=report.ReportError("report was already sent")
        ):
            with self.assertRaises(report.ReportError):
                report.run_report(
                    incident.incident_id,
                    recorder=recorder,
                    webhook_url="https://example.invalid/hook",
                    sender=lambda *_: self.fail("must not send"),
                )

    def test_missing_webhook_stops_before_claim(self):
        incident = self.recovered_incident()
        with patch.dict("os.environ", {}, clear=True), patch.object(
            report, "_claim_once"
        ) as claim:
            with self.assertRaises(report.ReportError):
                report.run_report(incident.incident_id, recorder=Recorder(incident))
        claim.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
