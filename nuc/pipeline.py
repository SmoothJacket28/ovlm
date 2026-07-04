"""
Core tracking pipeline — runs on the NUC.

Receives flushed frame windows from FrameBuffer, runs:
  - ROI ball tracking (both stereo cameras)
  - Seam-based spin estimation (dedicated high-fps spin camera when present,
    otherwise stereo camera 0)
  - Stereo triangulation
  - Robust ballistic trajectory fitting
  - Launch metric calculation
  - For inbound (kind == "pitch") events: break, release point, extension,
    and plate location via pitch_physics.py
and broadcasts results via WebSocket.
"""

import dataclasses
import logging
import time
from typing import List, Optional

import config
import ops243 as ops243_mod
import pitch_physics
from capture import FramePair
from seam_tracker import SeamTracker, SpinMeasurement
from tracker import BallTracker
from trajectory import TrajectoryFitter, LaunchMetrics
from triangulate import Point3D, Triangulator

log = logging.getLogger(__name__)

MPS_TO_MPH = 2.23694


class TrackingPipeline:
    def __init__(self, server, radar=None, ops243=None, spin_ring=None, store=None) -> None:
        self._server       = server
        self._radar        = radar       # optional IWR6843Reader (TI mmWave)
        self._ops243       = ops243      # optional OPS243Reader (OmniPreSense)
        self._spin_ring    = spin_ring   # optional SpinFrameRing (640 fps spin cam)
        self._store        = store       # optional SwingStore (durable archive)
        self._tracker0     = BallTracker()
        self._tracker1     = BallTracker()
        self._tracker_spin = BallTracker(
            min_radius=config.SPIN_BALL_MIN_RADIUS_PX,
            max_radius=config.SPIN_BALL_MAX_RADIUS_PX,
        )
        self._seam         = SeamTracker()
        self._triangulator = Triangulator()
        self._fitter       = TrajectoryFitter()

        if not self._triangulator.is_calibrated:
            log.warning(
                "No calibration file found (%s). Calibrate first for accurate "
                "measurements: plate_calib.py --live (just needs a home plate in "
                "view) or calibrate.py --live (ChArUco board).",
                config.CALIBRATION_FILE,
            )

    def process(self, frames: List[FramePair], trigger_time: float, kind: str = "hit") -> None:
        t_start = time.monotonic()
        log.info("Processing %d frame pairs … (kind=%s)", len(frames), kind)

        self._tracker0.reset()
        self._tracker1.reset()
        self._seam.reset()

        # Spin source: dedicated 640 fps camera when its window has frames,
        # otherwise fall back to stereo camera 0 inside the loop below.
        spin_frames = []
        if self._spin_ring is not None:
            spin_frames = self._spin_ring.window(
                trigger_time - config.HALF_WINDOW_S,
                trigger_time + config.HALF_WINDOW_S,
            )
        use_spin_cam = len(spin_frames) > 0
        spin_source = 'spincam' if use_spin_cam else 'stereo'

        points: List[Point3D] = []

        for frame_idx, pair in enumerate(frames):
            d0 = self._tracker0.update(pair.left,  frame_idx)
            d1 = self._tracker1.update(pair.right, frame_idx)

            # Feed seam tracker from camera 0 unless the spin camera covers it
            if d0 is not None and not use_spin_cam:
                self._seam.process_frame(pair.left, d0, pair.timestamp, frame_idx)

            if d0 is None or d1 is None:
                continue

            pt = self._triangulator.triangulate(
                d0.cx, d0.cy,
                d1.cx, d1.cy,
                pair.timestamp,
            )
            points.append(pt)

        # ── Tracking quality log ─────────────────────────────────────────────
        t0, t1 = self._tracker0, self._tracker1
        log.info(
            "Cam0: %d/%d detected (%d searched, %d tracked)  |  "
            "Cam1: %d/%d detected (%d searched, %d tracked)",
            t0.frames_detected, len(frames), t0.frames_searched, t0.frames_tracked,
            t1.frames_detected, len(frames), t1.frames_searched, t1.frames_tracked,
        )

        # ── Spin ─────────────────────────────────────────────────────────────
        if use_spin_cam:
            self._tracker_spin.reset()
            for idx, sf in enumerate(spin_frames):
                det = self._tracker_spin.update(sf.frame, idx)
                if det is not None:
                    self._seam.process_frame(sf.frame, det, sf.timestamp, idx)
            log.info(
                "Spin cam: %d/%d frames with ball detected",
                self._tracker_spin.frames_detected, len(spin_frames),
            )

        spin: Optional[SpinMeasurement] = self._seam.compute_spin()
        if spin:
            log.info(
                "Spin [%s]: %.0f rpm  axis=(%.2f, %.2f, %.2f)  eff=%.2f  conf=%.2f  frames=%d",
                spin_source, spin.spin_rate_rpm, *spin.spin_axis,
                spin.spin_efficiency, spin.confidence, spin.frames_analyzed,
            )
        else:
            log.info("Spin [%s]: insufficient seam data", spin_source)

        # ── Trajectory + metrics ─────────────────────────────────────────────
        latency_ms = (time.monotonic() - t_start) * 1000.0
        metrics: Optional[LaunchMetrics] = self._fitter.fit(points, latency_ms)

        if metrics is None:
            log.warning("Not enough 3D points (%d) — need at least 3", len(points))
            self._server.broadcast({"type": "status", "state": "armed"})
            return

        detect_rate = len(points) / max(len(frames), 1)

        # ── Radar cross-check ─────────────────────────────────────────────────
        radar_velocity_mps: Optional[float] = None
        pitch_velocity_mph: Optional[float] = None
        carry_distance_m:   Optional[float] = None
        ev_source = 'camera'

        # OPS243-C-FC-RP (primary radar — pitch speed, EV, FMCW range).
        # Readings are peak-of-event (release/contact speed convention) and
        # get cosine-corrected: Doppler measures only the radial component
        # v·cos(θ), so we divide by cos(θ) computed from the camera-fitted
        # geometry to recover true speed.
        ops_pitch_raw_mph: Optional[float] = None   # uncorrected, for the
        if self._ops243 is not None:                # precise pitch-branch fix-up
            ops_ev_mph    = self._ops243.latest_ev_mph()
            ops_pitch_raw_mph = self._ops243.latest_pitch_mph()
            ops_range_m   = self._ops243.latest_range_m()
            self._ops243.clear()

            if ops_pitch_raw_mph is not None:
                # Static geometry correction — refined below from the fitted
                # pitch trajectory when this event IS a pitch.
                pitch_velocity_mph = round(
                    ops_pitch_raw_mph / config.OPS243_PITCH_COS_DEFAULT, 1)

            if ops_range_m is not None:
                carry_distance_m = round(ops_range_m, 2)

            if ops_ev_mph is not None:
                # Cosine correction from the batted ball's launch direction:
                # evaluate the line-of-sight angle a few meters into the
                # flight, where the peak radar reading occurs.
                d = ops243_mod.launch_direction(
                    metrics.launch_angle_deg, metrics.spray_angle_deg)
                p0 = metrics.trajectory[0] if metrics.trajectory else None
                start = (p0.x, p0.y, p0.z) if p0 else (0.0, 1.0, 0.0)
                s = config.OPS243_EV_EVAL_DIST_M
                ball_pos = (start[0] + d[0] * s, start[1] + d[1] * s, start[2] + d[2] * s)
                cos_ev = ops243_mod.los_cosine(
                    d, ball_pos, config.OPS243_POS_M, floor=config.OPS243_COS_FLOOR)
                if cos_ev is not None:
                    corrected_mph = ops_ev_mph / cos_ev
                    log.info("OPS243 EV cosine correction: %.1f → %.1f mph (cosθ=%.3f)",
                             ops_ev_mph, corrected_mph, cos_ev)
                    ops_ev_mph = corrected_mph
                else:
                    log.info("OPS243 EV geometry too oblique for cosine "
                             "correction — using radial reading as-is")

                cam_mph = metrics.exit_velocity_mph
                agree   = abs(ops_ev_mph - cam_mph) / max(cam_mph, 1) <= config.OPS243_AGREE_FRACTION
                radar_velocity_mps = ops_ev_mph / MPS_TO_MPH
                if agree and cos_ev is not None:
                    metrics   = dataclasses.replace(metrics, exit_velocity_mph=round(ops_ev_mph, 1))
                    ev_source = 'radar'
                    log.info("OPS243 EV %.1f mph (camera=%.1f mph, Δ=%.1f%%)",
                             ops_ev_mph, cam_mph,
                             100 * abs(ops_ev_mph - cam_mph) / max(cam_mph, 1))
                elif not agree:
                    log.info("OPS243 EV %.1f mph disagrees with camera %.1f mph "
                             "(Δ=%.1f%% > %.0f%% tolerance) — keeping camera",
                             ops_ev_mph, cam_mph,
                             100 * abs(ops_ev_mph - cam_mph) / max(cam_mph, 1),
                             config.OPS243_AGREE_FRACTION * 100)
                else:
                    log.info("OPS243 EV %.1f mph uncorrectable geometry — "
                             "keeping camera %.1f mph", ops_ev_mph, cam_mph)

        # TI IWR6843ISK (optional secondary — higher spatial resolution)
        if self._radar is not None and ev_source == 'camera':
            frame = self._radar.latest_frame()
            if frame and frame.points:
                best = max(
                    (p for p in frame.points
                     if abs(p.vel) >= config.RADAR_TRIGGER_VELOCITY_MPS
                     and p.snr >= config.RADAR_MIN_SNR_DB),
                    key=lambda p: p.snr,
                    default=None,
                )
                if best is not None:
                    radar_mph = abs(best.vel) * MPS_TO_MPH
                    cam_mph   = metrics.exit_velocity_mph
                    agree     = abs(radar_mph - cam_mph) / max(cam_mph, 1) <= config.RADAR_AGREE_FRACTION
                    radar_velocity_mps = abs(best.vel)
                    if agree:
                        metrics   = dataclasses.replace(metrics, exit_velocity_mph=round(radar_mph, 1))
                        ev_source = 'radar'
                        log.info("IWR6843 EV %.1f mph (camera=%.1f mph, Δ=%.1f%%)",
                                 radar_mph, cam_mph,
                                 100 * abs(radar_mph - cam_mph) / max(cam_mph, 1))
                    else:
                        log.info(
                            "IWR6843 EV %.1f mph disagrees with camera %.1f mph "
                            "(Δ=%.1f%% > %.0f%% tolerance) — keeping camera",
                            radar_mph, cam_mph,
                            100 * abs(radar_mph - cam_mph) / max(cam_mph, 1),
                            config.RADAR_AGREE_FRACTION * 100,
                        )

        log.info(
            "EV=%.1f mph [%s]  LA=%.1f°  SA=%.1f°  residual=%.2f mm  "
            "latency=%.0f ms  detection=%.0f%%  fit=%d pts (%d rejected)",
            metrics.exit_velocity_mph, ev_source,
            metrics.launch_angle_deg, metrics.spray_angle_deg,
            metrics.fit_residual_mm, metrics.processing_latency_ms,
            detect_rate * 100,
            metrics.points_used, metrics.points_rejected,
        )

        # ── Pitch-specific physics: break, release point, extension, plate
        # location — only meaningful for an inbound (radar pitch-trigger) event.
        pitch_metrics: Optional[pitch_physics.PitchPhysics] = None
        if kind == "pitch":
            raw_fit = self._fitter.fit_raw(points)
            if raw_fit is not None:
                pitch_metrics = pitch_physics.compute_pitch_metrics(raw_fit, spin)

                # Refine the radar pitch speed with the PRECISE cosine
                # correction now that the pitch trajectory is fitted: the
                # line of sight runs from the antenna to the fitted release
                # point (the peak reading occurs near release, where the
                # ball is fastest). This replaces the static factor applied
                # earlier.
                if ops_pitch_raw_mph is not None:
                    cos_pitch = ops243_mod.los_cosine(
                        (raw_fit.vx0, raw_fit.vy0, raw_fit.vz0),
                        (raw_fit.x0,  raw_fit.y0,  raw_fit.z0),
                        config.OPS243_POS_M, floor=config.OPS243_COS_FLOOR)
                    if cos_pitch is not None:
                        corrected = round(ops_pitch_raw_mph / cos_pitch, 1)
                        log.info("OPS243 pitch cosine correction: %.1f → %.1f mph (cosθ=%.3f)",
                                 ops_pitch_raw_mph, corrected, cos_pitch)
                        pitch_velocity_mph = corrected

                # Prefer OPS243's Doppler reading when it agrees with the camera
                # (same agree/replace pattern as the EV cross-check above); fall
                # back to the camera-only estimate when radar isn't available.
                if pitch_metrics.camera_speed_mph is not None:
                    cam_pitch_mph = pitch_metrics.camera_speed_mph
                    if pitch_velocity_mph is not None:
                        delta = abs(pitch_velocity_mph - cam_pitch_mph) / max(cam_pitch_mph, 1)
                        if delta <= config.OPS243_AGREE_FRACTION:
                            log.info("OPS243 pitch %.1f mph (camera=%.1f mph, Δ=%.1f%%)",
                                     pitch_velocity_mph, cam_pitch_mph, 100 * delta)
                        else:
                            log.info("OPS243 pitch %.1f mph disagrees with camera %.1f mph "
                                      "(Δ=%.1f%% > %.0f%% tolerance) — keeping radar",
                                      pitch_velocity_mph, cam_pitch_mph, 100 * delta,
                                      config.OPS243_AGREE_FRACTION * 100)
                    else:
                        pitch_velocity_mph = cam_pitch_mph

                log.info(
                    "Pitch physics: VB=%s in  HB=%s in  SSW=(%s, %s) in  "
                    "release=(%.2f, %.2f) ft  ext=%.2f ft  plate=(%s, %s) ft",
                    pitch_metrics.vertical_break_in, pitch_metrics.horizontal_break_in,
                    pitch_metrics.ssw_break_v_in, pitch_metrics.ssw_break_h_in,
                    pitch_metrics.release_side_ft or 0.0, pitch_metrics.release_height_ft or 0.0,
                    pitch_metrics.extension_ft or 0.0,
                    pitch_metrics.plate_x_ft, pitch_metrics.plate_y_ft,
                )
            else:
                log.warning("Pitch event but fit_raw() failed — not enough/garbage points")

        payload = {
            "type":               "measurement",
            "exitVelocity":       metrics.exit_velocity_mph,
            "launchAngle":        metrics.launch_angle_deg,
            "sprayAngle":         metrics.spray_angle_deg,
            "fitResidualMm":      metrics.fit_residual_mm,
            "latencyMs":          metrics.processing_latency_ms,
            "detectRate":         round(detect_rate, 3),
            "pointsUsed":         metrics.points_used,
            "pointsRejected":     metrics.points_rejected,
            "evSource":           ev_source,
            "radarVelocityMps":   round(radar_velocity_mps, 3) if radar_velocity_mps is not None else None,
            "pitchVelocity":      pitch_velocity_mph,   # mph — OPS243 inbound reading, camera cross-checked/used as fallback
            "carryDistanceM":     carry_distance_m,     # meters, from OPS243 FMCW range
            "trajectory":         [
                {"x": p.x, "y": p.y, "z": p.z, "t": p.timestamp}
                for p in metrics.trajectory
            ],
            # Pitch movement / release — only populated for kind == "pitch"
            # (see pitch_physics.py); null for hit events, where they don't apply.
            "verticalBreakIn":   pitch_metrics.vertical_break_in if pitch_metrics else None,
            "horizontalBreakIn": pitch_metrics.horizontal_break_in if pitch_metrics else None,
            "sswBreakVIn":       pitch_metrics.ssw_break_v_in if pitch_metrics else None,
            "sswBreakHIn":       pitch_metrics.ssw_break_h_in if pitch_metrics else None,
            "releaseHeightFt":   pitch_metrics.release_height_ft if pitch_metrics else None,
            "releaseSideFt":     pitch_metrics.release_side_ft if pitch_metrics else None,
            "extensionFt":       pitch_metrics.extension_ft if pitch_metrics else None,
            "plateLocation": (
                {"xFt": pitch_metrics.plate_x_ft, "yFt": pitch_metrics.plate_y_ft}
                if pitch_metrics and pitch_metrics.plate_x_ft is not None and pitch_metrics.plate_y_ft is not None
                else None
            ),
        }

        if spin is not None:
            payload["spin"] = {
                "rpm":          spin.spin_rate_rpm,
                "axis":         list(spin.spin_axis),
                "efficiency":   spin.spin_efficiency,
                "confidence":   spin.confidence,
                "framesUsed":   spin.frames_analyzed,
                "source":       spin_source,
            }

        # Persist BEFORE broadcasting: the stored record gains a stable id
        # and timestamp, and both the archive and every connected dashboard
        # then agree on them (the dashboard dedupes history replays by id).
        # A storage failure must never block the live measurement.
        if self._store is not None:
            try:
                payload = self._store.append(payload, kind=kind)
            except Exception:
                log.exception("Swing store append failed — broadcasting unstored swing")

        self._server.broadcast(payload)
        self._server.broadcast({"type": "status", "state": "armed"})
