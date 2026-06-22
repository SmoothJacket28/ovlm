"""
Pitch-trajectory physics: turns a fitted release state + measured spin into
break, seam-shifted-wake residual, release point, extension, and the
plate-crossing location ("Strike Zone").

Two different sources feed this, and it's important not to blur them:

  - The "actual" plate crossing comes from RawFit's fitted polynomial
    (x(τ) = x0 + vx0·τ + qx·τ², etc.) extrapolated to z = 0 — i.e. whatever
    curvature the cameras actually measured. It is NOT a simulation.
  - "Zero-spin" and "Magnus-predicted" plate crossings ARE physics
    simulations (RK4 under gravity + quadratic drag, the latter also with
    Magnus lift from the measured spin) — reference baselines only.

Vertical/Horizontal Break = actual − zero-spin.
Seam-Shifted-Wake (SSW) Break = actual − Magnus-predicted, i.e. whatever the
real flight did beyond what spin-only physics predicts. The zero-spin term
cancels algebraically, so SSW reduces to (actual − magnus) directly.

Accuracy ceiling: the Magnus prediction (and therefore SSW) is only as good
as the measured spin axis, and seam_tracker.py already documents its axis
estimate as a single-camera placeholder (mostly ±Z with a small Y tilt, not
a solved 3-axis vector) — SSW residuals inherit that uncertainty until a
real dual-camera axis solve exists.

Release point uses the camera's FIRST detection (τ = 0), not an
extrapolated hand-release instant — extrapolating further back would
assume ballistic flight before the ball actually left the hand, which
isn't physically valid.
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

import config
from trajectory import RawFit, DRAG_K
from seam_tracker import SpinMeasurement

M_TO_FT    = 3.28084
M_TO_IN    = 39.3701
MPS_TO_MPH = 2.23694

_BALL_RADIUS_M = config.BALL_DIAMETER_M / 2.0
_BALL_AREA_M2  = math.pi * _BALL_RADIUS_M ** 2

_MAX_FLIGHT_S = 1.0     # generous ceiling — real pitches take ~0.35-0.5s mound-to-plate
_DT           = 0.0005  # 0.5 ms RK4 step
_MIN_SPEED_MPS = 5.0    # below this, treat the fit as noise, not a real pitch


@dataclass
class PitchPhysics:
    vertical_break_in:   Optional[float]
    horizontal_break_in: Optional[float]
    ssw_break_v_in:      Optional[float]
    ssw_break_h_in:      Optional[float]
    release_height_ft:   Optional[float]
    release_side_ft:     Optional[float]
    extension_ft:        Optional[float]
    plate_x_ft:          Optional[float]
    plate_y_ft:          Optional[float]
    camera_speed_mph:    Optional[float]


_NONE_RESULT = PitchPhysics(None, None, None, None, None, None, None, None, None, None)


def compute_pitch_metrics(raw: RawFit, spin: Optional[SpinMeasurement]) -> PitchPhysics:
    pos0 = np.array([raw.x0, raw.y0, raw.z0], dtype=np.float64)
    vel0 = np.array([raw.vx0, raw.vy0, raw.vz0], dtype=np.float64)
    speed = float(np.linalg.norm(vel0))

    # Guard against garbage fits (no real pitch, or ball already past the plate)
    if speed < _MIN_SPEED_MPS or raw.z0 <= 0:
        return _NONE_RESULT

    release_height_ft = raw.y0 * M_TO_FT
    release_side_ft   = raw.x0 * M_TO_FT
    extension_ft       = (config.RUBBER_DISTANCE_M - raw.z0) * M_TO_FT
    camera_speed_mph   = round(speed * MPS_TO_MPH, 1)

    actual    = _actual_plate_crossing(raw)
    zero_spin = _integrate_to_plate(pos0, vel0, omega=None)

    if actual is None or zero_spin is None:
        return PitchPhysics(
            vertical_break_in=None, horizontal_break_in=None,
            ssw_break_v_in=None, ssw_break_h_in=None,
            release_height_ft=round(release_height_ft, 2),
            release_side_ft=round(release_side_ft, 2),
            extension_ft=round(extension_ft, 2),
            plate_x_ft=None, plate_y_ft=None,
            camera_speed_mph=camera_speed_mph,
        )

    ax, ay, _ = actual
    zx, zy, _ = zero_spin
    vertical_break_in   = (ay - zy) * M_TO_IN
    horizontal_break_in = (ax - zx) * M_TO_IN

    omega_vec = _omega_from_spin(spin)
    ssw_v = ssw_h = None
    if omega_vec is not None:
        magnus = _integrate_to_plate(pos0, vel0, omega=omega_vec)
        if magnus is not None:
            mx, my, _ = magnus
            ssw_v = (ay - my) * M_TO_IN
            ssw_h = (ax - mx) * M_TO_IN

    return PitchPhysics(
        vertical_break_in=round(vertical_break_in, 1),
        horizontal_break_in=round(horizontal_break_in, 1),
        ssw_break_v_in=round(ssw_v, 1) if ssw_v is not None else None,
        ssw_break_h_in=round(ssw_h, 1) if ssw_h is not None else None,
        release_height_ft=round(release_height_ft, 2),
        release_side_ft=round(release_side_ft, 2),
        extension_ft=round(extension_ft, 2),
        plate_x_ft=round(ax * M_TO_FT, 2),
        plate_y_ft=round(ay * M_TO_FT, 2),
        camera_speed_mph=camera_speed_mph,
    )


def _omega_from_spin(spin: Optional[SpinMeasurement]) -> Optional[np.ndarray]:
    if spin is None or spin.spin_rate_rpm <= 0:
        return None
    axis = np.array(spin.spin_axis, dtype=np.float64)
    axis_norm = float(np.linalg.norm(axis))
    if axis_norm < 1e-6:
        return None
    omega_mag = spin.spin_rate_rpm * 2.0 * math.pi / 60.0
    return (axis / axis_norm) * omega_mag


def _actual_plate_crossing(raw: RawFit) -> Optional[Tuple[float, float, float]]:
    """Extrapolate the REAL fitted curve (not a simulation) to z = 0."""
    if abs(raw.qz) < 1e-9:
        if abs(raw.vz0) < 1e-9:
            return None
        tau = -raw.z0 / raw.vz0
    else:
        disc = raw.vz0 ** 2 - 4 * raw.qz * raw.z0
        if disc < 0:
            return None
        sq = math.sqrt(disc)
        candidates = [tt for tt in ((-raw.vz0 + sq) / (2 * raw.qz),
                                    (-raw.vz0 - sq) / (2 * raw.qz)) if tt > 0]
        if not candidates:
            return None
        tau = min(candidates)

    if tau <= 0 or tau > _MAX_FLIGHT_S:
        return None

    x = raw.x0 + raw.vx0 * tau + raw.qx * tau ** 2
    y_nogravity = raw.y0 + raw.vy0 * tau + raw.qy * tau ** 2
    y = y_nogravity - 0.5 * config.GRAVITY_M_S2 * tau ** 2
    return x, y, tau


def _accel(v: np.ndarray, omega: Optional[np.ndarray]) -> np.ndarray:
    speed = float(np.linalg.norm(v))
    a = np.array([0.0, -config.GRAVITY_M_S2, 0.0])
    if speed > 1e-6:
        a = a - DRAG_K * speed * v

    if omega is not None:
        omega_mag = float(np.linalg.norm(omega))
        if omega_mag > 1e-6 and speed > 1e-6:
            cross = np.cross(omega, v)
            cross_mag = float(np.linalg.norm(cross))
            if cross_mag > 1e-9:
                # Nathan's empirical lift-coefficient model: Cl(S), S = r·ω / v
                spin_factor = (_BALL_RADIUS_M * omega_mag) / speed
                cl = 1.0 / (2.32 + 0.4 / spin_factor)
                f_mag = cl * 0.5 * config.AIR_DENSITY * _BALL_AREA_M2 * speed ** 2
                a = a + (f_mag / config.BALL_MASS_KG) * (cross / cross_mag)
    return a


def _integrate_to_plate(
    pos0: np.ndarray, vel0: np.ndarray, omega: Optional[np.ndarray],
) -> Optional[Tuple[float, float, float]]:
    """RK4-integrate from `pos0` (z > 0, toward the mound) until z crosses 0."""
    pos, vel = pos0.copy(), vel0.copy()
    t = 0.0
    if pos[2] <= 0:
        return float(pos[0]), float(pos[1]), t

    def deriv(p: np.ndarray, v: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        return v, _accel(v, omega)

    steps = int(_MAX_FLIGHT_S / _DT)
    for _ in range(steps):
        prev_pos, prev_t = pos.copy(), t

        k1p, k1v = deriv(pos, vel)
        k2p, k2v = deriv(pos + _DT / 2 * k1p, vel + _DT / 2 * k1v)
        k3p, k3v = deriv(pos + _DT / 2 * k2p, vel + _DT / 2 * k2v)
        k4p, k4v = deriv(pos + _DT * k3p, vel + _DT * k3v)

        pos = pos + (_DT / 6) * (k1p + 2 * k2p + 2 * k3p + k4p)
        vel = vel + (_DT / 6) * (k1v + 2 * k2v + 2 * k3v + k4v)
        t += _DT

        if prev_pos[2] > 0 >= pos[2]:
            frac = prev_pos[2] / (prev_pos[2] - pos[2])
            x = prev_pos[0] + frac * (pos[0] - prev_pos[0])
            y = prev_pos[1] + frac * (pos[1] - prev_pos[1])
            tt = prev_t + frac * (t - prev_t)
            return float(x), float(y), float(tt)

    return None  # never reached the plate within _MAX_FLIGHT_S
