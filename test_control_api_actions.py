"""Mapping tests for the simulator's narrow in-process control endpoints."""

from __future__ import annotations

import unittest

from simulator import control_api


class RecordingControl:
    def __init__(self):
        self.calls = []

    def restart_encoder(self):
        self.calls.append(("restart_encoder",))
        return True, "restarted"

    def reduce_bitrate(self, factor):
        self.calls.append(("reduce_bitrate", factor))
        return True, "reduced"

    def switch_backup(self):
        self.calls.append(("switch_backup",))
        return True, "switched"

    def failover(self):
        self.calls.append(("failover",))
        return False, "failed safely"


class ControlApiActionTests(unittest.TestCase):
    def setUp(self):
        self.control = RecordingControl()
        control_api.app.state.pipeline_control = self.control

    def test_restart_encoder_mapping(self):
        result = control_api.control_restart_encoder()
        self.assertEqual(self.control.calls, [("restart_encoder",)])
        self.assertEqual(result, {
            "action": "RESTART_ENCODER", "ok": True, "detail": "restarted",
        })

    def test_reduce_profile_is_fixed_at_half_bitrate(self):
        result = control_api.control_reduce_profile()
        self.assertEqual(self.control.calls, [("reduce_bitrate", 0.5)])
        self.assertEqual(result["action"], "REDUCE_PROFILE")

    def test_switch_source_mapping(self):
        result = control_api.control_switch_source()
        self.assertEqual(self.control.calls, [("switch_backup",)])
        self.assertEqual(result["action"], "SWITCH_SOURCE")

    def test_failover_mapping_preserves_method_failure(self):
        result = control_api.control_failover()
        self.assertEqual(self.control.calls, [("failover",)])
        self.assertEqual(result, {
            "action": "FAILOVER", "ok": False, "detail": "failed safely",
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
