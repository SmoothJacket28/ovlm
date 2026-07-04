"""
Integration test: OPS243 radar fusion inside the real TrackingPipeline.

Drives TrackingPipeline.process() end-to-end with synthetic ballistic
trajectories (camera side) and synthetic radar serial lines fed through the
real OPS243Reader parser (radar side), then asserts on the broadcast
measurement payload: peak-of-event capture, cosine correction, agreement
gating, and pitch-velocity refinement from the fitted release geometry.

Run from the nuc/ directory:
    python -m unittest tests.test_pipeline_radar -v
Needs numpy; no cameras, serial hardware, or OpenCV.
"""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Stub cv2 before any project import (config/capture/triangulate import it).
# MagicMock is fully permissive: constants, type annotations, and calls like
# cv2.createBackgroundSubtractorMOG2(...) all resolve at import/construct time.
if 'cv2' not in sys.modules:
    from unittest import mock
    sys.modules['cv2'] = mock.MagicMock(name='cv2-stub')

import numpy as np

import config
import ops243
import pipeline as pipeline_mod
from capture import FramePair
from ops243 import OPS243Reader
from triangulate import Point3D

MPS_TO_MPH = 2.23694
G = 9.81


# ── Test doubles ──────────────────────────────────────────────────────────────

class FakeServer:
    def __init__(self):
        self.messages = []

    def broadcast(self, msg):
        self.messages.append(msg)

    def measurement(self):
        ms = [m for m in self.messages if m.get('type') == 'measurement']
        return ms[-1] if ms else None


class FakeDetection:
    cx, cy, radius = 320.0, 240.0, 12.0


class FakeTracker:
    """Always 'detects' the ball; the triangulator supplies the real points."""
    frames_detected = frames_searched = frames_tracked = 0

    def reset(self):
        pass

    def update(self, frame, idx):
        self.frames_detected += 1
        return FakeDetection()


class FakeSeam:
    def reset(self):
        pass

    def process_frame(self, *a, **k):
        pass

    def compute_spin(self):
        return None


class ScriptedTriangulator:
    """Returns a precomputed ballistic trajectory point per frame."""
    is_calibrated = True

    def __init__(self, points):
        self._points = list(points)
        self._i = 0

    def triangulate(self, *a, **k):
        p = self._points[self._i]
        self._i += 1
        return p


def ballistic_points(p0, v0, n=25, fps=210.0):
    """Point3D samples of a drag-free ballistic flight (plate frame)."""
    pts = []
    for i in range(n):
        t = i / fps
        pts.append(Point3D(
            x=p0[0] + v0[0] * t,
            y=p0[1] + v0[1] * t - 0.5 * G * t * t,
            z=p0[2] + v0[2] * t,
            timestamp=t,
        ))
    return pts


def make_reader():
    return OPS243Reader(
        port='/dev/null', baud=9600,
        min_pitch=13.4, min_ev=17.9, debounce_s=0.5,
        min_magnitude=0.0, event_window_s=1.2, fresh_s=2.5,
    )


def build_pipeline(points, reader):
    server = FakeServer()
    pipe = pipeline_mod.TrackingPipeline(server, radar=None, ops243=reader, spin_ring=None)
    pipe._tracker0 = FakeTracker()
    pipe._tracker1 = FakeTracker()
    pipe._seam = FakeSeam()
    pipe._triangulator = ScriptedTriangulator(points)
    return pipe, server


def dummy_frames(n=25, fps=210.0):
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    return [FramePair(left=img, right=img, timestamp=i / fps) for i in range(n)]


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestHitFusion(unittest.TestCase):
    TRUE_EV_MPH = 100.0

    def _launch_vector(self, la_deg=25.0, sa_deg=-10.0):
        v = self.TRUE_EV_MPH / MPS_TO_MPH
        la, sa = math.radians(la_deg), math.radians(sa_deg)
        return (v * math.cos(la) * math.sin(sa),
                v * math.sin(la),
                v * math.cos(la) * math.cos(sa))

    def _run(self, radar_lines):
        p0 = (0.0, 1.0, 0.0)
        v0 = self._launch_vector()
        pts = ballistic_points(p0, v0)
        reader = make_reader()
        pipe, server = build_pipeline(pts, reader)
        for line in radar_lines:
            reader._parse(line)
        pipe.process(dummy_frames(), trigger_time=0.0, kind="hit")
        return server.measurement(), v0, p0

    def test_radar_ev_cosine_corrected_and_preferred(self):
        # Radial reading = true speed × cos(θ) at the configured eval point.
        v0 = self._launch_vector()
        p0 = (0.0, 1.0, 0.0)
        d = ops243.launch_direction(25.0, -10.0)
        s = config.OPS243_EV_EVAL_DIST_M
        ball = (p0[0] + d[0] * s, p0[1] + d[1] * s, p0[2] + d[2] * s)
        cos_ev = ops243.los_cosine(d, ball, config.OPS243_POS_M)
        self.assertIsNotNone(cos_ev)

        true_mps = self.TRUE_EV_MPH / MPS_TO_MPH
        radial_peak = true_mps * cos_ev
        # Decaying series after the peak — drag while flying away.
        lines = [f'{radial_peak:.2f}', f'{radial_peak - 0.4:.2f}',
                 f'{radial_peak - 1.1:.2f}', f'{radial_peak - 2.0:.2f}']
        msg, _, _ = self._run(lines)

        self.assertIsNotNone(msg)
        self.assertEqual(msg['evSource'], 'radar')
        # Corrected radar EV must land on the true speed, not the radial one.
        self.assertAlmostEqual(msg['exitVelocity'], self.TRUE_EV_MPH, delta=0.6)

    def test_disagreeing_radar_keeps_camera(self):
        # Radar sees something 40% slow (e.g. a ball fragment / bat) — the
        # agreement gate must keep the camera EV.
        msg, _, _ = self._run(['26.8'])   # ~60 mph radial vs ~100 camera
        self.assertIsNotNone(msg)
        self.assertEqual(msg['evSource'], 'camera')
        self.assertAlmostEqual(msg['exitVelocity'], self.TRUE_EV_MPH, delta=1.5)

    def test_no_radar_readings_uses_camera(self):
        msg, _, _ = self._run([])
        self.assertIsNotNone(msg)
        self.assertEqual(msg['evSource'], 'camera')
        self.assertIsNone(msg['pitchVelocity'])

    def test_hit_event_pitch_speed_static_correction(self):
        # During a hitting session the radar also saw the incoming pitch;
        # the payload carries it with the static cosine factor applied.
        radial_pitch_mps = 35.0   # ~78.3 mph radial
        msg, _, _ = self._run([f'-{radial_pitch_mps:.2f}'])
        self.assertIsNotNone(msg)
        expected = round((radial_pitch_mps * MPS_TO_MPH)
                         / config.OPS243_PITCH_COS_DEFAULT, 1)
        self.assertAlmostEqual(msg['pitchVelocity'], expected, places=1)


class TestPitchFusion(unittest.TestCase):
    TRUE_PITCH_MPH = 90.0

    def _run(self):
        release = (0.3, 1.8, 16.5)
        speed = self.TRUE_PITCH_MPH / MPS_TO_MPH
        direction = np.array([-0.005, -0.05, -1.0])
        direction /= np.linalg.norm(direction)
        v0 = tuple(speed * direction)
        pts = ballistic_points(release, v0)

        cos_pitch = ops243.los_cosine(v0, release, config.OPS243_POS_M)
        self.assertIsNotNone(cos_pitch)
        radial_peak = speed * cos_pitch

        reader = make_reader()
        pipe, server = build_pipeline(pts, reader)
        # Inbound = negative; decaying magnitudes after the release peak.
        for r in (radial_peak, radial_peak - 0.5, radial_peak - 1.2):
            reader._parse(f'-{r:.2f}')
        pipe.process(dummy_frames(), trigger_time=0.0, kind="pitch")
        return server.measurement(), cos_pitch

    def test_pitch_velocity_precisely_corrected(self):
        msg, cos_pitch = self._run()
        self.assertIsNotNone(msg)
        # The precise fit-based correction must recover the true release
        # speed from the radial reading.
        self.assertAlmostEqual(msg['pitchVelocity'], self.TRUE_PITCH_MPH, delta=0.5)
        # And the correction must actually matter (radial ≠ true).
        radial_mph = self.TRUE_PITCH_MPH * cos_pitch
        self.assertNotAlmostEqual(radial_mph, self.TRUE_PITCH_MPH, places=1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
