"""
Unit tests for the durable swing store — append/read round-trip, torn-line
tolerance, compaction, monthly rotation, and identity assignment.

Run from the nuc/ directory:
    python -m unittest tests.test_swing_store -v
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if 'cv2' not in sys.modules:
    from unittest import mock
    sys.modules['cv2'] = mock.MagicMock(name='cv2-stub')

from swing_store import SwingStore, _compact


def sample_payload(ev=98.4):
    return {
        'type': 'measurement',
        'exitVelocity': ev,
        'launchAngle': 24.0,
        'sprayAngle': -8.0,
        'fitResidualMm': 3.14159265,
        'latencyMs': 180.0,
        'detectRate': 0.86,
        'evSource': 'radar',
        'pitchVelocity': None,
        'plateLocation': None,
        'trajectory': [
            {'x': 0.123456789, 'y': 1.000000001, 'z': 0.0, 't': 0.0},
            {'x': 0.323456789, 'y': 1.200000001, 'z': 0.4, 't': 0.0047619},
        ],
    }


class TestSwingStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SwingStore(directory=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_assigns_identity_and_persists(self):
        rec = self.store.append(sample_payload(), kind='hit')
        self.assertIn('id', rec)
        self.assertIn('timestamp', rec)
        self.assertEqual(rec['kind'], 'hit')
        loaded = self.store.load_recent(10)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]['id'], rec['id'])
        self.assertEqual(loaded[0]['exitVelocity'], 98.4)

    def test_round_trip_order_oldest_first(self):
        ids = [self.store.append(sample_payload(ev))['id'] for ev in (90.0, 95.0, 100.0)]
        loaded = self.store.load_recent(10)
        self.assertEqual([r['id'] for r in loaded], ids)

    def test_limit_returns_newest(self):
        for ev in range(80, 90):
            self.store.append(sample_payload(float(ev)))
        loaded = self.store.load_recent(3)
        self.assertEqual([r['exitVelocity'] for r in loaded], [87.0, 88.0, 89.0])

    def test_nulls_dropped_and_floats_rounded(self):
        rec = self.store.append(sample_payload())
        self.assertNotIn('pitchVelocity', rec)     # null dropped
        self.assertNotIn('plateLocation', rec)
        self.assertEqual(rec['trajectory'][0]['x'], 0.123)   # 3 decimals
        self.assertEqual(rec['fitResidualMm'], 3.142)

    def test_torn_last_line_skipped_earlier_records_intact(self):
        r1 = self.store.append(sample_payload(90.0))
        self.store.append(sample_payload(95.0))
        # Simulate a crash mid-append: truncate into the last line.
        path = next(self.store.directory.glob('*.jsonl'))
        data = path.read_bytes()
        path.write_bytes(data[:-25])
        loaded = self.store.load_recent(10)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]['id'], r1['id'])

    def test_garbage_lines_skipped(self):
        self.store.append(sample_payload(90.0))
        path = next(self.store.directory.glob('*.jsonl'))
        with open(path, 'a') as f:
            f.write('not json at all\n')
            f.write('[1,2,3]\n')       # valid JSON, wrong shape
        self.store.append(sample_payload(95.0))
        loaded = self.store.load_recent(10)
        self.assertEqual([r['exitVelocity'] for r in loaded], [90.0, 95.0])

    def test_monthly_rotation_reads_across_files(self):
        jan = dict(sample_payload(90.0), timestamp=int(time.mktime(
            time.strptime('2026-01-15', '%Y-%m-%d'))) * 1000)
        feb = dict(sample_payload(95.0), timestamp=int(time.mktime(
            time.strptime('2026-02-15', '%Y-%m-%d'))) * 1000)
        self.store.append(jan)
        self.store.append(feb)
        files = sorted(p.name for p in self.store.directory.glob('*.jsonl'))
        self.assertEqual(files, ['2026-01.jsonl', '2026-02.jsonl'])
        loaded = self.store.load_recent(10)
        self.assertEqual([r['exitVelocity'] for r in loaded], [90.0, 95.0])

    def test_compact_record_is_small(self):
        # Space guard: a full 25-point record must stay under 1.5 KB.
        payload = sample_payload()
        payload['trajectory'] = [
            {'x': i * 0.2113456, 'y': 1 + i * 0.09987, 'z': i * 0.4356, 't': i / 210}
            for i in range(25)
        ]
        rec = self.store.append(payload)
        line = json.dumps(rec, separators=(',', ':'))
        self.assertLess(len(line), 1500)

    def test_count_and_disk_usage(self):
        self.assertEqual(self.store.count(), 0)
        self.store.append(sample_payload())
        self.store.append(sample_payload())
        self.assertEqual(self.store.count(), 2)
        self.assertGreater(self.store.disk_usage_bytes(), 0)

    def test_compact_helper(self):
        self.assertEqual(
            _compact({'a': None, 'b': 1.23456, 'c': [{'d': None, 'e': 2.0}]}, 3),
            {'b': 1.235, 'c': [{'e': 2.0}]},
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
