from __future__ import annotations

import unittest

import knowledge_base
from external_calls import (
    AI_HTTP_OPTIONS,
    EXTERNAL_AI_TIMEOUT_SECONDS,
    ExternalCallTimeout,
    is_timeout_error,
)


class ExternalCallDeadlineTests(unittest.TestCase):
    def test_sdk_http_timeout_matches_configured_deadline(self):
        self.assertEqual(
            AI_HTTP_OPTIONS.timeout,
            round(EXTERNAL_AI_TIMEOUT_SECONDS * 1000),
        )

    def test_timeout_detection_handles_wrapped_deadline(self):
        try:
            try:
                raise TimeoutError("request timed out")
            except TimeoutError as cause:
                raise RuntimeError("SDK request failed") from cause
        except RuntimeError as exc:
            self.assertTrue(is_timeout_error(exc))

    def test_embedding_timeout_retries_once_then_stops(self):
        calls = []

        def operation():
            calls.append(1)
            raise TimeoutError("timed out")

        with self.assertRaises(ExternalCallTimeout):
            knowledge_base._embedding_request(operation)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
