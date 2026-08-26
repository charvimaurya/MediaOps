"""
No real FFmpeg required -- PipelineControl takes a fake pipeline handle
(anything with .state_lock, .current_state, .restart_ffmpeg()) and a fake
backup instance (anything with .state, .baseline_cpu, .baseline_memory,
.start(), .restart()).
"""

import threading

import pytest

from detector import rules
from simulator.control import PipelineControl
from simulator.failures import MediaState, healthy_state, encoder_overload, network_degradation, encoder_failure


class FakePipeline:
    def __init__(self, state):
        self.current_state = state
        self.state_lock = threading.Lock()
        self.restart_ffmpeg_result = True
        self.restart_ffmpeg_calls = 0

    def restart_ffmpeg(self) -> bool:
        self.restart_ffmpeg_calls += 1
        return self.restart_ffmpeg_result


class FakeBackup:
    def __init__(self):
        self.state = MediaState(
            fps=30.0, cpu_usage=20.0, memory_usage=30.0, bitrate=5.2,
            encoding_latency=40.0, dropped_frames=0.1, packet_loss=0.1,
            network_latency=30.0, encoder_status=1, failure_mode="healthy",
            encoder_fault="healthy", network_fault=False, bitrate_factor=1.0,
            active_output="backup",
        )
        self.baseline_cpu = 20.0
        self.baseline_memory = 30.0
        self.start_result = True
        self.restart_result = True
        self.start_calls = 0
        self.restart_calls = 0

    def start(self) -> bool:
        self.start_calls += 1
        return self.start_result

    def restart(self) -> bool:
        self.restart_calls += 1
        return self.restart_result


def make_control(state, backup=None):
    fake = FakePipeline(state)
    control = PipelineControl(pipeline_handle=fake, backup_instance=backup or FakeBackup())
    return fake, control


def _telemetry_dict(state) -> dict:
    return {
        "fps": state.fps,
        "cpu_usage": state.cpu_usage,
        "encoding_latency": state.encoding_latency,
        "dropped_frames": state.dropped_frames,
        "encoder_status": state.encoder_status,
    }


def test_restart_encoder_resets_encoder_state_and_status():
    fake, control = make_control(encoder_overload())

    success, detail = control.restart_encoder()

    assert success is True
    assert fake.current_state.encoder_fault == "healthy"
    assert fake.current_state.encoder_status == 1
    assert rules.is_healthy(_telemetry_dict(fake.current_state)) is True


def test_restart_encoder_does_not_clear_network_fault():
    fake, control = make_control(network_degradation())

    success, detail = control.restart_encoder()

    assert success is True  # the restart itself succeeds...
    assert fake.current_state.network_fault is True  # ...but the network fault remains
    assert rules.is_healthy(_telemetry_dict(fake.current_state)) is False


def test_reduce_bitrate_lowers_target_and_records_previous():
    fake, control = make_control(network_degradation())

    success, detail = control.reduce_bitrate(0.5)

    assert success is True
    assert fake.current_state.bitrate_factor == 0.5
    assert fake.current_state.previous_bitrate_factor == 1.0
    assert fake.current_state.bitrate < 5.2


def test_reduce_bitrate_does_not_fully_resolve_encoder_overload():
    fake, control = make_control(encoder_overload())

    success, detail = control.reduce_bitrate(0.0)  # maximum possible relief

    assert success is True
    assert rules.is_healthy(_telemetry_dict(fake.current_state)) is False
    assert fake.current_state.cpu_usage >= 90  # pinned above threshold regardless


def test_reduce_bitrate_at_default_factor_does_not_resolve_network_degradation():
    # Tuned in this phase so the default factor (0.5) is deliberately
    # insufficient -- see simulator/failures.py's network-layer formula.
    fake, control = make_control(network_degradation())

    success, detail = control.reduce_bitrate()  # default factor

    assert success is True
    assert rules.is_healthy(_telemetry_dict(fake.current_state)) is False


def test_switch_backup_changes_active_output_and_telemetry_reflects_standby():
    fake, control = make_control(encoder_overload())

    success, detail = control.switch_backup()

    assert success is True
    assert fake.current_state.active_output == "backup"
    # backup's own encoder is healthy -- overload was primary-only
    assert rules.is_healthy(_telemetry_dict(fake.current_state)) is True


def test_switch_backup_does_not_resolve_network_fault():
    fake, control = make_control(network_degradation())

    success, detail = control.switch_backup()

    assert success is True
    assert fake.current_state.active_output == "backup"
    assert rules.is_healthy(_telemetry_dict(fake.current_state)) is False


@pytest.mark.parametrize("preset", [encoder_overload, network_degradation, encoder_failure])
def test_failover_recovers_every_fault_type(preset):
    fake, control = make_control(preset())

    success, detail = control.failover()

    assert success is True
    assert fake.current_state.active_output == "backup"
    assert rules.is_healthy(_telemetry_dict(fake.current_state)) is True


def test_restart_encoder_returns_false_cleanly_when_process_does_not_come_up():
    fake, control = make_control(encoder_failure())
    fake.restart_ffmpeg_result = False

    success, detail = control.restart_encoder()

    assert success is False
    assert "did not come up" in detail


def test_switch_backup_returns_false_cleanly_when_standby_does_not_come_up():
    backup = FakeBackup()
    backup.start_result = False
    fake, control = make_control(encoder_overload(), backup=backup)

    success, detail = control.switch_backup()

    assert success is False
    assert "did not come up" in detail


def test_switch_backup_is_idempotent():
    fake, control = make_control(encoder_overload())

    control.switch_backup()
    success, detail = control.switch_backup()  # called again

    assert success is True
    assert fake.current_state.active_output == "backup"


def test_restart_encoder_called_twice_does_not_corrupt_state():
    fake, control = make_control(encoder_overload())

    control.restart_encoder()
    success, detail = control.restart_encoder()

    assert success is True
    assert fake.current_state.encoder_fault == "healthy"
    assert fake.restart_ffmpeg_calls == 2
