"""
OmniPreSense OPS243-C-FC-RP radar driver for OVLM.

Hardware connection
-------------------
  USB-A cable: OPS243 → NUC USB port
  Linux device: /dev/ttyACM0  (or /dev/ttyACM1 if cameras claimed ACM0)
  macOS:        /dev/cu.usbmodem*  (ls /dev/cu.usbmodem* with the radar plugged in)
  Windows:      COM5 (check Device Manager → Ports)

The OPS243 is a USB CDC-ACM device — the configured baud rate is cosmetic;
data moves at USB speed regardless.

Configuration (sent at startup, per OmniPreSense AN-010 API):
  UM      speed units m/s (SI — we convert to mph ourselves)
  S2      20 ksps sampling → max measurable speed 62.2 m/s (139.1 mph).
          CRITICAL: the factory default 10 ksps (SX) tops out at 31.1 m/s
          (69.5 mph) — pitched and batted baseballs exceed that, so without
          S2 the sensor cannot report most real pitch/exit velocities.
  F2      two decimal places (~0.01 m/s reported precision)
  R|      report both directions (inbound pitches AND outbound batted balls)
  R>n     on-device minimum-speed filter — drops sub-threshold noise (fans,
          people, bat waggle) before it ever reaches the serial link
  OM      report signal magnitude with each speed — used to reject weak
          multipath/ghost readings in software
  OJ      JSON output — unambiguous parsing (named fields, explicit
          direction) instead of sign conventions on bare numbers

Line formats handled by the parser (newest firmware first):
  {"speed":38.22,"direction":"outbound","magnitude":212.5}   OJ mode
  {"range":7.62,...}                                          FMCW range (C variant)
  -35.61            plain m/s   (negative = inbound = pitch)
  212.5,38.22       magnitude,speed pair (OM without OJ)

Accuracy model
--------------
A Doppler radar measures RADIAL speed only, and a ball's over-the-ground
speed decays from drag while the radar keeps reporting. Two consequences:

  1. Within one event the TRUE launch/release speed is the PEAK reading,
     not the latest — a batted ball loses ~10% of its speed in the first
     150 ft of flight. The driver therefore keeps a timestamped buffer per
     direction and exposes peak-of-event accessors with spike rejection
     (a lone reading >5% above every other reading in the event is treated
     as noise unless it's the only reading).
  2. The radial projection understates true speed by cos(θ), where θ is
     the angle between the ball's velocity and the radar line of sight.
     los_cosine() computes that correction factor from the camera-fitted
     trajectory; pipeline.py divides radar speeds by it.

Usage
-----
  reader = OPS243Reader()
  reader.start()
  reader.set_trigger_callback(lambda speed_mph, range_m: ...)
  ...
  reader.stop()
"""

import json
import logging
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional, Sequence, Tuple

import config as cfg

log = logging.getLogger(__name__)

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

MPS_TO_MPH = 2.23694

# Plain-text fallback:  "-35.61"  or  "212.5,38.22" (magnitude,speed with OM)
_NUM = r'[+-]?\d+(?:\.\d+)?'
_PLAIN_RE = re.compile(rf'^({_NUM})(?:,({_NUM}))?$')

TriggerCallback = Callable[[float, Optional[float]], None]  # (speed_mph, range_m)


@dataclass
class Reading:
    t: float            # time.monotonic() at parse
    speed_mps: float    # absolute radial speed
    magnitude: Optional[float]  # signal strength; None if not reported


@dataclass
class EventPeak:
    speed_mps: float
    n_readings: int     # corroborating readings in the event window
    t: float            # timestamp of the peak reading


def los_cosine(
    vel: Sequence[float],
    ball_pos: Sequence[float],
    radar_pos: Sequence[float],
    floor: float = 0.5,
) -> Optional[float]:
    """cos(θ) between the ball's velocity and the radar line of sight.

    Divide a radial Doppler speed by this to recover true speed. Returns
    None when the geometry is too oblique to correct reliably (cos < floor)
    or degenerate (zero-length vectors) — callers should then use the
    radial reading as-is rather than amplify noise.

    Coordinates are the OVLM plate frame (x lateral, y up, z toward the
    pitcher); any consistent frame works.
    """
    vx, vy, vz = float(vel[0]), float(vel[1]), float(vel[2])
    lx = float(ball_pos[0]) - float(radar_pos[0])
    ly = float(ball_pos[1]) - float(radar_pos[1])
    lz = float(ball_pos[2]) - float(radar_pos[2])
    v_norm = math.sqrt(vx * vx + vy * vy + vz * vz)
    l_norm = math.sqrt(lx * lx + ly * ly + lz * lz)
    if v_norm < 1e-9 or l_norm < 1e-9:
        return None
    c = abs((vx * lx + vy * ly + vz * lz) / (v_norm * l_norm))
    if c < floor:
        return None
    return c


def launch_direction(launch_angle_deg: float, spray_angle_deg: float) -> Tuple[float, float, float]:
    """Unit velocity direction from launch/spray angles (plate frame,
    matching trajectory.py: LA = atan2(vy, horiz), SA = atan2(vx, vz))."""
    la = math.radians(launch_angle_deg)
    sa = math.radians(spray_angle_deg)
    return (math.cos(la) * math.sin(sa), math.sin(la), math.cos(la) * math.cos(sa))


class OPS243Reader:
    """Serial driver for the OPS243-C-FC-RP (Doppler + FMCW variant)."""

    def __init__(
        self,
        port:        str   = cfg.OPS243_PORT,
        baud:        int   = cfg.OPS243_BAUD,
        min_pitch:   float = cfg.OPS243_MIN_PITCH_MPS,
        min_ev:      float = cfg.OPS243_MIN_EV_MPS,
        debounce_s:  float = cfg.AUDIO_DEBOUNCE_S,
        min_magnitude: float = getattr(cfg, 'OPS243_MIN_MAGNITUDE', 0.0),
        event_window_s: float = getattr(cfg, 'OPS243_EVENT_WINDOW_S', 1.2),
        fresh_s:     float = getattr(cfg, 'OPS243_FRESH_S', 2.5),
    ) -> None:
        self._port       = port
        self._baud       = baud
        self._min_pitch  = min_pitch   # m/s inbound threshold
        self._min_ev     = min_ev      # m/s outbound threshold
        self._debounce   = debounce_s
        self._min_mag    = min_magnitude
        self._window     = event_window_s
        self._fresh      = fresh_s

        self._running    = False
        self._thread: Optional[threading.Thread] = None
        self._trigger_cb: Optional[TriggerCallback] = None
        self._last_trigger = 0.0
        self._pitch_trigger_cb: Optional[TriggerCallback] = None
        self._last_pitch_trigger = 0.0

        self._lock = threading.Lock()
        self._inbound:  Deque[Reading] = deque(maxlen=128)   # pitches
        self._outbound: Deque[Reading] = deque(maxlen=128)   # batted balls
        self._ranges:   Deque[Tuple[float, float]] = deque(maxlen=128)  # (t, range_m)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_trigger_callback(self, cb: TriggerCallback) -> None:
        """Fires on each outbound detection above min_ev (exit velocity event)."""
        self._trigger_cb = cb

    def set_pitch_trigger_callback(self, cb: TriggerCallback) -> None:
        """Fires on each inbound detection above min_pitch (pitch-release event)."""
        self._pitch_trigger_cb = cb

    def pitch_event(self) -> Optional[EventPeak]:
        """Peak inbound speed of the current event (= release speed, the
        pitch-velocity convention). None if no fresh readings."""
        with self._lock:
            return self._event_peak(self._inbound)

    def ev_event(self) -> Optional[EventPeak]:
        """Peak outbound speed of the current event (closest to contact,
        before drag decays it — the exit-velocity convention)."""
        with self._lock:
            return self._event_peak(self._outbound)

    # Back-compat accessors (peak-of-event semantics, mph)
    def latest_pitch_mph(self) -> Optional[float]:
        ev = self.pitch_event()
        return None if ev is None else ev.speed_mps * MPS_TO_MPH

    def latest_ev_mph(self) -> Optional[float]:
        ev = self.ev_event()
        return None if ev is None else ev.speed_mps * MPS_TO_MPH

    def latest_range_m(self) -> Optional[float]:
        """Farthest FMCW range in the fresh window — the best carry proxy
        (the ball is tracked flying away; max range = last point on flight)."""
        now = time.monotonic()
        with self._lock:
            fresh = [r for t, r in self._ranges if now - t <= self._fresh]
        return max(fresh) if fresh else None

    def clear(self) -> None:
        """Reset stored readings — call after a measurement is consumed."""
        with self._lock:
            self._inbound.clear()
            self._outbound.clear()
            self._ranges.clear()

    def start(self) -> None:
        if not SERIAL_AVAILABLE:
            raise RuntimeError("pyserial not installed — run: pip install pyserial")
        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop, daemon=True, name="ops243-reader"
        )
        self._thread.start()
        log.info("OPS243 reader started on %s", self._port)

    def stop(self) -> None:
        self._running = False

    # ── Event peak extraction ──────────────────────────────────────────────────

    def _event_peak(self, buf: Deque[Reading]) -> Optional[EventPeak]:
        """Robust peak of the most recent event window. Caller holds lock."""
        now = time.monotonic()
        fresh = [r for r in buf if now - r.t <= self._fresh]
        if not fresh:
            return None
        # The event = readings within `window` of the newest reading
        t_last = fresh[-1].t
        event = [r for r in fresh if t_last - r.t <= self._window]
        if len(event) == 1:
            r = event[0]
            return EventPeak(speed_mps=r.speed_mps, n_readings=1, t=r.t)
        # Spike rejection: accept the max only if a second reading is within
        # 5% of it; otherwise use the highest corroborated reading.
        speeds = sorted((r for r in event), key=lambda r: r.speed_mps, reverse=True)
        for i, r in enumerate(speeds):
            others = speeds[:i] + speeds[i + 1:]
            if any(abs(o.speed_mps - r.speed_mps) <= 0.05 * r.speed_mps for o in others):
                return EventPeak(speed_mps=r.speed_mps, n_readings=len(event), t=r.t)
        # No two readings agree (all scattered) — fall back to the median
        mid = speeds[len(speeds) // 2]
        return EventPeak(speed_mps=mid.speed_mps, n_readings=len(event), t=mid.t)

    # ── Serial reader loop ─────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        try:
            with serial.Serial(self._port, self._baud, timeout=0.1) as port:
                self._configure(port)
                while self._running:
                    line = port.readline().decode('ascii', errors='ignore').strip()
                    if line:
                        self._parse(line)
        except serial.SerialException as exc:
            log.error("OPS243 serial error: %s", exc)
        finally:
            log.info("OPS243 reader loop exited.")

    def _configure(self, port: "serial.Serial") -> None:
        """Send startup commands (OmniPreSense AN-010 API)."""
        # On-device speed filter: drop anything slower than the lowest speed
        # we care about in either direction.
        min_speed = int(min(self._min_pitch, self._min_ev))
        cmds: List[bytes] = [
            b'UM',                                # units: m/s
            cfg.OPS243_SAMPLE_RATE_CMD.encode(),  # S2 = 20 ksps → 139 mph max
            b'F2',                                # 2 decimal places
            b'R|',                                # report both directions
            f'R>{min_speed}'.encode(),            # on-device min-speed filter
            b'OM',                                # report magnitude with speed
            b'OJ',                                # JSON output (unambiguous)
        ]
        for cmd in cmds:
            port.write(cmd)
            time.sleep(0.1)
            # Drain (and log) the device's acknowledgement lines so they are
            # not misparsed as speed readings.
            ack = port.read(port.in_waiting or 1).decode('ascii', errors='ignore').strip()
            if ack:
                log.debug("OPS243 %s → %s", cmd.decode(), ack.replace('\r\n', ' | '))
        log.info("OPS243 configured: m/s, 20 ksps (139 mph max), R>%d filter, "
                 "magnitude + JSON output", min_speed)

    # ── Parsing ────────────────────────────────────────────────────────────────

    def _parse(self, line: str) -> None:
        speed_mps: Optional[float] = None   # signed: negative = inbound
        magnitude: Optional[float] = None
        range_m:   Optional[float] = None

        if line.startswith('{'):
            try:
                obj = json.loads(line)
            except ValueError:
                return
            if not isinstance(obj, dict):
                return
            # Range-only lines (FMCW) and combined lines both handled
            rng = obj.get('range', obj.get('distance'))
            if isinstance(rng, (int, float)):
                range_m = abs(float(rng))
            spd = obj.get('speed')
            if isinstance(spd, (int, float)):
                speed_mps = float(spd)
                direction = obj.get('direction')
                if isinstance(direction, str):
                    # Explicit direction beats sign conventions
                    speed_mps = -abs(speed_mps) if direction.lower().startswith('in') else abs(speed_mps)
            mag = obj.get('magnitude')
            if isinstance(mag, (int, float)):
                magnitude = float(mag)
            # Ignore acks/info lines ({"Product":...}, {"Units":...}, …)
            if speed_mps is None and range_m is None:
                return
        else:
            m = _PLAIN_RE.match(line)
            if not m:
                return
            a = float(m.group(1))
            b = float(m.group(2)) if m.group(2) else None
            if b is None:
                speed_mps = a
            else:
                # Two-field CSV: with OM enabled the device emits
                # "magnitude,speed"; a legacy "speed,range" pairing is only
                # assumed when the first field is negative (a signed speed
                # can never be a magnitude). Positive-first lines parse as
                # magnitude,speed — the conservative reading, since a
                # misread ghost EV is worse than a dropped range value.
                if a >= 0:
                    magnitude, speed_mps = a, b
                else:
                    speed_mps, range_m = a, abs(b)

        now = time.monotonic()

        if range_m is not None:
            with self._lock:
                self._ranges.append((now, range_m))

        if speed_mps is None:
            return

        # Software magnitude gate — reject weak multipath/ghost readings.
        if magnitude is not None and self._min_mag > 0 and magnitude < self._min_mag:
            log.debug("OPS243 rejected weak reading: %.2f m/s mag=%.1f < %.1f",
                      speed_mps, magnitude, self._min_mag)
            return

        # Negative = ball approaching sensor (pitched ball coming in)
        # Positive = ball moving away from sensor (batted ball going out)
        if speed_mps < -self._min_pitch:
            reading = Reading(t=now, speed_mps=abs(speed_mps), magnitude=magnitude)
            with self._lock:
                self._inbound.append(reading)
            pitch_mph = reading.speed_mps * MPS_TO_MPH
            log.debug("Pitch: %.1f mph  mag=%s", pitch_mph,
                      f"{magnitude:.0f}" if magnitude is not None else "—")
            self._maybe_trigger_pitch(pitch_mph, range_m)

        elif speed_mps > self._min_ev:
            reading = Reading(t=now, speed_mps=speed_mps, magnitude=magnitude)
            with self._lock:
                self._outbound.append(reading)
            ev_mph = reading.speed_mps * MPS_TO_MPH
            log.debug("EV: %.1f mph  mag=%s  range: %s m", ev_mph,
                      f"{magnitude:.0f}" if magnitude is not None else "—",
                      f"{range_m:.1f}" if range_m else "—")
            self._maybe_trigger(ev_mph, range_m)

    def _maybe_trigger(self, ev_mph: float, range_m: Optional[float]) -> None:
        if not self._trigger_cb:
            return
        now = time.monotonic()
        if now - self._last_trigger >= self._debounce:
            self._last_trigger = now
            self._trigger_cb(ev_mph, range_m)

    def _maybe_trigger_pitch(self, pitch_mph: float, range_m: Optional[float]) -> None:
        if not self._pitch_trigger_cb:
            return
        now = time.monotonic()
        if now - self._last_pitch_trigger >= self._debounce:
            self._last_pitch_trigger = now
            self._pitch_trigger_cb(pitch_mph, range_m)
