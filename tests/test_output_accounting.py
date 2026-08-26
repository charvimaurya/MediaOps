import os
import tempfile
import time

import pytest

from simulator.output_accounting import OutputAccounting, SEGMENT_DURATION_SECONDS


def _write_segment(output_dir: str, index: int, mtime: float, size: int = 1024) -> str:
    path = os.path.join(output_dir, f"segment_{index:05d}.ts")
    with open(path, "wb") as f:
        f.write(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def output_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_no_missing_segments_during_healthy_operation(output_dir):
    stream_start = time.time() - 10.0  # stream "started" 10s ago
    accounting = OutputAccounting([output_dir], stream_start=stream_start)

    # 5 segments at the expected cadence -- stream ran healthy for 10s / 2s = 5 segments
    for i in range(5):
        _write_segment(output_dir, i, mtime=stream_start + i * SEGMENT_DURATION_SECONDS)

    snapshot = accounting.snapshot()
    assert snapshot.missing == 0
    assert snapshot.segments_written == 5


def test_dead_encoder_produces_measurable_gap(output_dir):
    stream_start = time.time() - 10.0
    accounting = OutputAccounting([output_dir], stream_start=stream_start)

    # Only 2 segments written (encoder died after ~4s) despite 10s having
    # elapsed -- no file ever appears for the rest of the window.
    _write_segment(output_dir, 0, mtime=stream_start + 0)
    _write_segment(output_dir, 1, mtime=stream_start + SEGMENT_DURATION_SECONDS)

    snapshot = accounting.snapshot()
    assert snapshot.segments_written == 2
    assert snapshot.missing > 0


def test_gap_between_is_correct_over_a_partial_window(output_dir):
    stream_start = 1000.0
    accounting = OutputAccounting([output_dir], stream_start=stream_start)

    # Segments at t=0,2,4,6,8,10,12 (7 segments across 14s)
    for i in range(7):
        _write_segment(output_dir, i, mtime=stream_start + i * SEGMENT_DURATION_SECONDS)

    # Window covering t=4..10 (inclusive both ends) -- catches segments
    # at 4, 6, 8, 10 (4 segments), against 3 expected over that 6s slice
    # (gap_between only counts "expected" via elapsed time, not the
    # inclusive endpoint, so written can exceed expected here -- that's
    # fine, missing is clamped at 0, never negative).
    gap = accounting.gap_between(stream_start + 4, stream_start + 10)

    assert gap.expected == 3
    assert gap.written == 4
    assert gap.missing == 0


def test_gap_between_reports_missing_over_a_partial_window_with_a_gap(output_dir):
    stream_start = 2000.0
    accounting = OutputAccounting([output_dir], stream_start=stream_start)

    # Segments only at t=0 and t=2 -- nothing written between t=4 and t=10.
    _write_segment(output_dir, 0, mtime=stream_start + 0)
    _write_segment(output_dir, 1, mtime=stream_start + SEGMENT_DURATION_SECONDS)

    gap = accounting.gap_between(stream_start + 4, stream_start + 10)

    assert gap.written == 0
    assert gap.expected == 3
    assert gap.missing == 3


def test_counters_do_not_go_negative_and_do_not_double_count_on_backup_switch():
    with tempfile.TemporaryDirectory() as primary_dir, tempfile.TemporaryDirectory() as backup_dir:
        stream_start = time.time() - 8.0
        accounting = OutputAccounting([primary_dir, backup_dir], stream_start=stream_start)

        # Primary wrote segments 0-1 before a switch; backup wrote
        # segments 0-1 (its own independent numbering) after the switch.
        _write_segment(primary_dir, 0, mtime=stream_start + 0)
        _write_segment(primary_dir, 1, mtime=stream_start + SEGMENT_DURATION_SECONDS)
        _write_segment(backup_dir, 0, mtime=stream_start + 4)
        _write_segment(backup_dir, 1, mtime=stream_start + 6)

        snapshot = accounting.snapshot()

        # 4 distinct files across 2 directories -- summed once each, not
        # double-counted just because both directories are scanned.
        assert snapshot.segments_written == 4
        assert snapshot.missing >= 0
        assert snapshot.segments_expected >= 0


def test_missing_and_expected_never_negative_for_a_stream_that_just_started():
    accounting = OutputAccounting(["/nonexistent/path"], stream_start=time.time())
    snapshot = accounting.snapshot()
    assert snapshot.segments_written == 0
    assert snapshot.segments_expected >= 0
    assert snapshot.missing >= 0


def test_nonexistent_output_dir_counts_as_zero_written_not_an_error():
    accounting = OutputAccounting(["/definitely/does/not/exist"], stream_start=time.time() - 5.0)
    snapshot = accounting.snapshot()
    assert snapshot.segments_written == 0
