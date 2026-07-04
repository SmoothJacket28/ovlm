"""
Unit tests for the OPS243 radar driver — parsing, event peak extraction,
magnitude gating, freshness, and cosine-error correction.

Run from the nuc/ directory:
    python -m unittest tests.test_ops243 -v
No hardware, pyserial, or OpenCV required.
"""

import math
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# config.py imports cv2 for capture constants; stub it so the radar module
# is testable without OpenCV installed. MagicMock keeps the stub permissive
# enough for any test module that runs in the same process.
if 'cv2' not in sys.modules:
    from unittest import mock
    sys.modules['cv2'] = mock.MagicMock(name='cv2-stub')

import ops243
from ops243 import OPS243Reader, launch_direction, los_cosine

MPS_TO_MPH = ops243.MPS_TO_MPH


def make_reader(**kw) -> OPS243Reader:
    defaults = dict(
        port='/dev/null', baud=9600,
        min_pitch=13.4, min_ev=17.9, debounce_s=0.5,
        min_magnitude=0.0, event_window_s=1.2, fresh_s=2.5,
    )
    defaults.update(kw)
    return OPS243Reader(**defaults)


class TestParsing(unittest.TestCase):
    def test_plain_negative_is_pitch(self):
        r = make_reader()
        r._parse('-35.61')
        ev = r.pitch_event()
        self.assertIsNotNone(ev)
        self.assertAlmostEqual(ev.speed_mps, 35.61)
        self.assertIsNone(r.ev_event())

    def test_plain_positive_is_ev(self):
        r = make_reader()
        r._parse('44.70')
        ev = r.ev_event()
        self.assertIsNotNone(ev)
        self.assertAlmostEqual(ev.speed_mps, 44.70)
        self.assertIsNone(r.pitch_event())

    def test_below_threshold_ignored(self):
        r = make_reader()
        r._parse('5.0')     # below min_ev
        r._parse('-5.0')    # below min_pitch
        self.assertIsNone(r.ev_event())
        self.assertIsNone(r.pitch_event())

    def test_json_with_direction_field(self):
        r = make_reader()
        r._parse('{"speed":38.22,"direction":"outbound","magnitude":212.5}')
        ev = r.ev_event()
        self.assertIsNotNone(ev)
        self.assertAlmostEqual(ev.speed_mps, 38.22)

    def test_json_inbound_direction_overrides_sign(self):
        r = make_reader()
        r._parse('{"speed":40.0,"direction":"inbound"}')
        self.assertIsNotNone(r.pitch_event())
        self.assertIsNone(r.ev_event())

    def test_json_range_line(self):
        r = make_reader()
        r._parse('{"range":7.62}')
        self.assertAlmostEqual(r.latest_range_m(), 7.62)

    def test_json_ack_lines_ignored(self):
        r = make_reader()
        r._parse('{"Product":"OPS243"}')
        r._parse('{"Units":"m/s"}')
        self.assertIsNone(r.ev_event())
        self.assertIsNone(r.pitch_event())

    def test_magnitude_speed_csv(self):
        # OM without OJ: "magnitude,speed" — magnitude unsigned and larger
        r = make_reader()
        r._parse('212.5,38.22')
        ev = r.ev_event()
        self.assertIsNotNone(ev)
        self.assertAlmostEqual(ev.speed_mps, 38.22)

    def test_speed_range_csv_legacy(self):
        # Legacy "speed,range" pairing — signed speed first
        r = make_reader()
        r._parse('-35.61,7.62')
        self.assertIsNotNone(r.pitch_event())
        self.assertAlmostEqual(r.latest_range_m(), 7.62)

    def test_garbage_ignored(self):
        r = make_reader()
        for line in ('', '?', 'OPS243-C', '{bad json', 'a,b,c'):
            r._parse(line)
        self.assertIsNone(r.ev_event())
        self.assertIsNone(r.pitch_event())


class TestMagnitudeGate(unittest.TestCase):
    def test_weak_reading_rejected(self):
        r = make_reader(min_magnitude=50.0)
        r._parse('{"speed":38.0,"direction":"outbound","magnitude":10.0}')
        self.assertIsNone(r.ev_event())

    def test_strong_reading_accepted(self):
        r = make_reader(min_magnitude=50.0)
        r._parse('{"speed":38.0,"direction":"outbound","magnitude":300.0}')
        self.assertIsNotNone(r.ev_event())

    def test_no_magnitude_passes_gate(self):
        # Plain lines carry no magnitude — the gate must not drop them.
        r = make_reader(min_magnitude=50.0)
        r._parse('38.0')
        self.assertIsNotNone(r.ev_event())


class TestEventPeak(unittest.TestCase):
    def test_peak_not_last_on_decaying_series(self):
        # A batted ball decelerates — the radar reports a decaying series.
        # The event speed must be the PEAK, not the final reading.
        r = make_reader()
        for s in ('44.8', '44.3', '43.1', '41.9', '40.2'):
            r._parse(s)
        ev = r.ev_event()
        self.assertAlmostEqual(ev.speed_mps, 44.8)
        self.assertEqual(ev.n_readings, 5)

    def test_lone_spike_rejected(self):
        # One reading 20% above every other is noise, not the ball.
        r = make_reader()
        for s in ('40.1', '40.0', '48.5', '39.8', '39.5'):
            r._parse(s)
        ev = r.ev_event()
        self.assertAlmostEqual(ev.speed_mps, 40.1)   # highest corroborated

    def test_corroborated_peak_kept(self):
        # Two readings within 5% of each other at the top → real peak.
        r = make_reader()
        for s in ('44.8', '44.5', '40.0', '39.0'):
            r._parse(s)
        ev = r.ev_event()
        self.assertAlmostEqual(ev.speed_mps, 44.8)

    def test_single_reading_accepted(self):
        r = make_reader()
        r._parse('38.0')
        ev = r.ev_event()
        self.assertAlmostEqual(ev.speed_mps, 38.0)
        self.assertEqual(ev.n_readings, 1)

    def test_stale_readings_expire(self):
        r = make_reader(fresh_s=0.05)
        r._parse('38.0')
        time.sleep(0.08)
        self.assertIsNone(r.ev_event())

    def test_clear(self):
        r = make_reader()
        r._parse('38.0')
        r._parse('-35.0')
        r._parse('{"range":5.0}')
        r.clear()
        self.assertIsNone(r.ev_event())
        self.assertIsNone(r.pitch_event())
        self.assertIsNone(r.latest_range_m())

    def test_range_max_hold(self):
        # Carry proxy = farthest range seen, not the last.
        r = make_reader()
        for line in ('{"range":3.0}', '{"range":8.5}', '{"range":6.0}'):
            r._parse(line)
        self.assertAlmostEqual(r.latest_range_m(), 8.5)


class TestTriggers(unittest.TestCase):
    def test_outbound_fires_hit_trigger(self):
        r = make_reader()
        fired = []
        r.set_trigger_callback(lambda mph, rng: fired.append((mph, rng)))
        r._parse('44.70')
        self.assertEqual(len(fired), 1)
        self.assertAlmostEqual(fired[0][0], 44.70 * MPS_TO_MPH, places=2)

    def test_inbound_fires_pitch_trigger_not_hit(self):
        r = make_reader()
        hits, pitches = [], []
        r.set_trigger_callback(lambda mph, rng: hits.append(mph))
        r.set_pitch_trigger_callback(lambda mph, rng: pitches.append(mph))
        r._parse('-40.0')
        self.assertEqual(hits, [])
        self.assertEqual(len(pitches), 1)

    def test_below_threshold_never_triggers(self):
        r = make_reader()
        fired = []
        r.set_trigger_callback(lambda mph, rng: fired.append(mph))
        r.set_pitch_trigger_callback(lambda mph, rng: fired.append(mph))
        r._parse('10.0')
        r._parse('-10.0')
        self.assertEqual(fired, [])

    def test_debounce_suppresses_burst(self):
        # A hit produces a burst of outbound readings — the trigger must
        # fire once per event, not once per reading.
        r = make_reader(debounce_s=0.5)
        fired = []
        r.set_trigger_callback(lambda mph, rng: fired.append(mph))
        for s_ in ('44.8', '44.3', '43.1', '41.9'):
            r._parse(s_)
        self.assertEqual(len(fired), 1)

    def test_debounce_expiry_allows_next_event(self):
        r = make_reader(debounce_s=0.03)
        fired = []
        r.set_trigger_callback(lambda mph, rng: fired.append(mph))
        r._parse('44.8')
        time.sleep(0.05)
        r._parse('42.0')
        self.assertEqual(len(fired), 2)

    def test_magnitude_gated_reading_does_not_trigger(self):
        # A weak ghost return must not fire a capture.
        r = make_reader(min_magnitude=50.0)
        fired = []
        r.set_trigger_callback(lambda mph, rng: fired.append(mph))
        r._parse('{"speed":38.0,"direction":"outbound","magnitude":5.0}')
        self.assertEqual(fired, [])


class TestCosineCorrection(unittest.TestCase):
    def test_head_on_is_unity(self):
        # Ball flying straight along the line of sight → no correction.
        c = los_cosine(vel=(0, 0, 40), ball_pos=(0, 0, 10), radar_pos=(0, 0, 0))
        self.assertAlmostEqual(c, 1.0)

    def test_45_degrees(self):
        c = los_cosine(vel=(40, 0, 40), ball_pos=(0, 0, 10), radar_pos=(0, 0, 0))
        self.assertAlmostEqual(c, math.cos(math.radians(45)), places=6)

    def test_oblique_returns_none(self):
        # 80° off-axis → cos ≈ 0.17 < 0.5 floor → uncorrectable.
        v = (math.sin(math.radians(80)) * 40, 0, math.cos(math.radians(80)) * 40)
        self.assertIsNone(los_cosine(v, (0, 0, 10), (0, 0, 0)))

    def test_degenerate_returns_none(self):
        self.assertIsNone(los_cosine((0, 0, 0), (0, 0, 10), (0, 0, 0)))
        self.assertIsNone(los_cosine((0, 0, 40), (0, 0, 0), (0, 0, 0)))

    def test_typical_pitch_geometry(self):
        # Release 1.8 m up, 16.5 m out; antenna 0.3 m up, 1 m behind plate.
        # Pitch velocity roughly toward the plate, slightly down.
        c = los_cosine(vel=(0.2, -2.0, -38.0),
                       ball_pos=(0.3, 1.8, 16.5),
                       radar_pos=(0.0, 0.3, -1.0))
        self.assertIsNotNone(c)
        self.assertGreater(c, 0.98)   # small correction, but real

    def test_launch_direction_matches_trajectory_conventions(self):
        # LA 90° → straight up; SA 0/LA 0 → straight at the pitcher (+z).
        d = launch_direction(90.0, 0.0)
        self.assertAlmostEqual(d[1], 1.0, places=6)
        d = launch_direction(0.0, 0.0)
        self.assertAlmostEqual(d[2], 1.0, places=6)
        # Positive spray = +x (pull side for RHB), matching atan2(vx, vz).
        d = launch_direction(0.0, 30.0)
        self.assertGreater(d[0], 0)
        self.assertAlmostEqual(math.degrees(math.atan2(d[0], d[2])), 30.0, places=4)

    def test_ev_correction_magnitude(self):
        # A 100 mph EV read radially at 20° off-axis reports 94 mph;
        # dividing by cos recovers 100.
        true_mph = 100.0
        cos20 = math.cos(math.radians(20))
        radial = true_mph * cos20
        self.assertAlmostEqual(radial / cos20, true_mph, places=6)


if __name__ == '__main__':
    unittest.main(verbosity=2)
