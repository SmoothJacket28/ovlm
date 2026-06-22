/**
 * Pure display-derived metrics computed from already-measured data —
 * nothing here is stored on BallMeasurement, it's recomputed on read.
 */

import type { BallMeasurement, SeamMeasurement } from '@/types/tracking';

const G         = 9.81;     // m/s²
const DRAG_K    = 0.55;     // empirical drag factor (fly balls)
const ROLL_K    = 0.55;     // empirical roll factor (ground balls, seconds of roll)
const MPH_TO_MS = 0.44704;
const M_TO_FT   = 3.281;

/** True spin: the portion of total spin that drives Magnus movement (excludes gyro/bullet spin). */
export function trueSpinRateRpm(seam: SeamMeasurement): number {
  return seam.spinRate * seam.spinEfficiency;
}

/** 0° = pure transverse spin (100% efficient), 90° = pure gyro (football) spin. */
export function gyroDegree(seam: SeamMeasurement): number {
  const eff = Math.max(0, Math.min(1, seam.spinEfficiency));
  return Math.acos(eff) * (180 / Math.PI);
}

/**
 * Spin axis as a clock face, viewed from the catcher looking at the pitcher
 * (world frame: +Y = up, +Z = toward the mound, +X = toward first base).
 * A pure backspin/topspin axis lies along X (vertical-break pitches, the
 * 12-6 line); a pure side-spin axis lies along Y (horizontal-break, 3-9).
 */
export function spinClockLabel(axis: [number, number, number]): string {
  const [x, y] = axis;
  if (x === 0 && y === 0) return '—';
  let deg = Math.atan2(y, x) * (180 / Math.PI);
  if (deg < 0) deg += 360;
  const totalMinutes = Math.round(deg / 30 * 60); // 30° per clock-hour
  let hour = Math.floor(totalMinutes / 60) % 12;
  const minute = totalMinutes % 60;
  if (hour === 0) hour = 12;
  return `${hour}:${String(minute).padStart(2, '0')}`;
}

/** Estimated landing distance in feet — radar FMCW range when available, else a physics estimate. */
export function calculatedDistanceFt(ball: BallMeasurement): number {
  if (ball.carryDistanceM != null) return ball.carryDistanceM * M_TO_FT;
  return Math.hypot(...landingFt(ball.exitVelocity, ball.launchAngle, ball.sprayAngle));
}

/** Top-down landing coordinates in feet, home plate at the origin (+x = right, -y = outfield). */
export function landingFt(ev: number, la: number, sa: number): [number, number] {
  const v     = ev * MPH_TO_MS;
  const laRad = la * (Math.PI / 180);
  const saRad = sa * (Math.PI / 180);

  let rangeFt: number;
  if (la <= 0) {
    // Ground ball / line drive below horizontal
    const rollFactor = Math.max(0.05, 1 + la / 30);
    rangeFt = v * ROLL_K * rollFactor * M_TO_FT;
  } else {
    const rangeM = (v * v * Math.sin(2 * laRad) / G) * DRAG_K;
    rangeFt = rangeM * M_TO_FT;
  }

  const x =  rangeFt * Math.sin(saRad);
  const z = -rangeFt * Math.cos(saRad);   // SVG: outfield is −y
  return [x, z];
}

/** Highest point of the batted ball's tracked flight, in feet. Null if no trajectory was captured. */
export function apexHeightFt(ball: BallMeasurement): number | null {
  if (ball.trajectory.length === 0) return null;
  return Math.max(...ball.trajectory.map((p) => p.y)) * M_TO_FT;
}
