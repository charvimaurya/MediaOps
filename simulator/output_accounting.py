"""
Segment accounting for RPO measurement.

Output is written as real HLS segments (simulator/pipeline.py's and
simulator/backup.py's ffmpeg invocations, both at SEGMENT_DURATION_SECONDS
cadence). segments_written is always a live scan of the output
directories -- file existence + non-zero size -- never synthesised from a
health flag. If an encoder is dead, no new file appears there, and the
gap this module reports is real.

Scans BOTH the primary and backup output directories (rather than just
"whichever is currently active") so a mid-incident switch doesn't lose
credit for segments a now-inactive instance genuinely wrote earlier in
the window, and so nothing is double-counted -- each segment file exists
in exactly one directory.
"""

import glob
import os
import time
from dataclasses import dataclass
from typing import List

from shared_metrics.metrics import SEGMENTS_EXPECTED_TOTAL, SEGMENTS_WRITTEN_TOTAL

SEGMENT_DURATION_SECONDS = 2.0


@dataclass
class AccountingSnapshot:
    segments_expected: int
    segments_written: int
    missing: int


@dataclass
class GapResult:
    expected: int
    written: int
    missing: int


class OutputAccounting:
    def __init__(self, output_dirs: List[str], stream_start: float = None):
        self._output_dirs = list(output_dirs)
        self._stream_start = stream_start if stream_start is not None else time.time()
        self._last_pushed_expected = 0
        self._last_pushed_written = 0

    def _segment_files(self):
        files = []
        for output_dir in self._output_dirs:
            if not output_dir or not os.path.isdir(output_dir):
                continue
            for path in glob.glob(os.path.join(output_dir, "segment_*.ts")):
                try:
                    if os.path.getsize(path) > 0:
                        files.append(path)
                except OSError:
                    continue  # file removed mid-scan -- not a real segment
        return files

    def _expected_as_of(self, timestamp: float) -> int:
        elapsed = max(0.0, timestamp - self._stream_start)
        return int(elapsed // SEGMENT_DURATION_SECONDS)

    def snapshot(self) -> AccountingSnapshot:
        now = time.time()
        written = len(self._segment_files())
        expected = self._expected_as_of(now)
        missing = max(0, expected - written)

        # Push deltas to the Counters (section G) -- both quantities are
        # monotonically non-decreasing within one run (expected is pure
        # elapsed-time math; written only grows since nothing deletes
        # segment files mid-run), so inc(delta) is always >= 0.
        if expected > self._last_pushed_expected:
            SEGMENTS_EXPECTED_TOTAL.inc(expected - self._last_pushed_expected)
            self._last_pushed_expected = expected
        if written > self._last_pushed_written:
            SEGMENTS_WRITTEN_TOTAL.inc(written - self._last_pushed_written)
            self._last_pushed_written = written

        return AccountingSnapshot(segments_expected=expected, segments_written=written, missing=missing)

    def gap_between(self, start: float, end: float) -> GapResult:
        if end < start:
            start, end = end, start

        files = self._segment_files()
        written_in_window = 0
        for path in files:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if start <= mtime <= end:
                written_in_window += 1

        expected_in_window = max(0, self._expected_as_of(end) - self._expected_as_of(start))
        missing = max(0, expected_in_window - written_in_window)

        return GapResult(expected=expected_in_window, written=written_in_window, missing=missing)
