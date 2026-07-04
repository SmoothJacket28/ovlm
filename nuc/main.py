"""
OVLM — Open Vision Launch Monitor
Entry point for the Windows NUC pipeline.

Usage:
    python main.py [--no-audio] [--debug]
"""

import argparse
import asyncio
import logging
import signal
import sys
import time

import dataclasses

import config
from audio_trigger import AudioTrigger
from calib_session import CalibSession
from capture import SpinCapturer, StereoCapturer
from frame_buffer import FrameBuffer, SpinFrameRing
from pipeline import TrackingPipeline
from ops243 import OPS243Reader
from radar import IWR6843Reader
from server import PipelineServer
from swing_store import SwingStore


def main() -> None:
    parser = argparse.ArgumentParser(description="OVLM NUC pipeline")
    parser.add_argument("--no-audio", action="store_true",
                        help="Disable mic trigger (manual arm via browser)")
    parser.add_argument("--ops243", action="store_true",
                        help="Enable OPS243-C-FC-RP radar (pitch speed + EV + carry distance)")
    parser.add_argument("--radar", action="store_true",
                        help="Enable TI IWR6843ISK radar (also triggers on ball detection)")
    parser.add_argument("--spin-cam", action="store_true",
                        help="Enable the optional high-fps spin camera (config.SPIN_CAM_*)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("main")

    server   = PipelineServer()

    # ── OPS243 radar (plug-and-play: pitch speed + EV + carry distance) ──────
    ops243: OPS243Reader | None = None
    if args.ops243 or config.OPS243_ENABLED:
        try:
            ops243 = OPS243Reader()
            ops243.start()
            log.info("OPS243 radar started on %s", config.OPS243_PORT)
        except Exception as exc:
            log.warning("OPS243 not available (%s) — continuing without it", exc)
            ops243 = None

    # ── TI IWR6843ISK radar (optional, binary mmWave) ─────────────────────────
    radar: IWR6843Reader | None = None
    if args.radar or config.RADAR_ENABLED:
        radar = IWR6843Reader()
        radar.start()
        log.info("IWR6843 radar started")

    # ── Spin camera (optional) ────────────────────────────────────────────────
    spin_ring: SpinFrameRing | None = None
    spin_cap:  SpinCapturer  | None = None
    if args.spin_cam or config.SPIN_CAM_ENABLED:
        spin_ring = SpinFrameRing()
        spin_cap  = SpinCapturer(on_frame=spin_ring.push)

    # Durable swing archive — every measurement is fsync'd to monthly JSONL
    # files before broadcast (see swing_store.py). Survives crashes, restarts,
    # and power loss; the dashboard replays it on connect via get_history.
    store: SwingStore | None = None
    if config.SWING_STORE_ENABLED:
        store = SwingStore(directory=config.SWING_STORE_DIR,
                           trajectory_decimals=config.SWING_TRAJECTORY_DECIMALS)
        log.info("Swing store: %s (%d swings, %.1f KB)",
                 store.directory, store.count(), store.disk_usage_bytes() / 1024)

    pipeline = TrackingPipeline(server, radar=radar, ops243=ops243, spin_ring=spin_ring,
                                store=store)
    buffer   = FrameBuffer(on_flush=pipeline.process)
    capturer = StereoCapturer(on_pair=buffer.push)

    armed = False
    current_mode = "hitting"   # 'pitching' | 'hitting' | 'live' — set by the browser via set_mode

    def hit_trigger_active() -> bool:
        return current_mode in ("hitting", "live")

    def pitch_trigger_active() -> bool:
        return current_mode in ("pitching", "live")

    def set_mode(mode: str) -> None:
        nonlocal current_mode
        if mode not in ("pitching", "hitting", "live"):
            return
        current_mode = mode
        log.info("Session mode -> %s", mode)

    def arm():
        nonlocal armed
        armed = True
        if not args.no_audio and hit_trigger_active():
            audio.arm()
        server.broadcast({"type": "status", "state": "armed",
                          "audioArmed": not args.no_audio and hit_trigger_active()})
        log.info("Armed (mode=%s)", current_mode)

    def disarm():
        nonlocal armed
        armed = False
        if not args.no_audio:
            audio.disarm()
        server.broadcast({"type": "status", "state": "idle", "audioArmed": False})
        log.info("Disarmed")

    def reset():
        buffer.clear()
        if spin_ring is not None:
            spin_ring.clear()
        server.broadcast({"type": "status", "state": "armed"})
        log.info("Reset")

    if not args.no_audio:
        audio = AudioTrigger(on_trigger=lambda t: buffer.trigger(t) if armed else None)
        try:
            audio.start()
            log.info("Audio trigger started")
        except Exception as exc:
            log.warning("Audio trigger not available (%s) — continuing without it", exc)
    else:
        audio = None

    # Radar trigger: fires the same buffer.trigger() path as audio.
    # buffer.trigger() expects a monotonic timestamp, not the radar velocity.
    if radar is not None:
        def _radar_trigger(pt) -> None:
            if armed:
                log.info("Radar trigger at %.1f mph", abs(pt.vel) * 2.23694)
                buffer.trigger(time.monotonic())
        radar.set_trigger_callback(_radar_trigger)

    # OPS243 hit trigger — PRIMARY trigger for Hitting/Live sessions.
    # Fires on outbound (batted-ball) detections, which are physically
    # selective: only a batted ball moves away above the EV threshold, so
    # it has none of the mic's failure modes (cage noise false-fires,
    # missed triggers on quiet/soft contact, gain sensitivity). The mic
    # stays wired as a redundant backup — either trigger flushes the same
    # frame window, and a re-trigger merely re-centers it. The radar
    # report lags contact by ~0.1 s, so the trigger time is backdated to
    # keep the ±window centered on the hit.
    if ops243 is not None:
        def _ops243_hit_trigger(ev_mph: float, _range_m: float | None) -> None:
            if armed and hit_trigger_active():
                log.info("Radar hit trigger at %.1f mph", ev_mph)
                buffer.trigger(time.monotonic() - config.OPS243_TRIGGER_LATENCY_S)
        ops243.set_trigger_callback(_ops243_hit_trigger)

    # OPS243 pitch trigger: fires on inbound (pitch-release) detections, the
    # same buffer.trigger() path as audio — used for Pitching/Live sessions,
    # which have no bat-crack for the mic to listen for.
    if ops243 is not None:
        def _ops243_pitch_trigger(pitch_mph: float, _range_m: float | None) -> None:
            if armed and pitch_trigger_active():
                log.info("Radar pitch trigger at %.1f mph", pitch_mph)
                buffer.trigger(time.monotonic(), kind="pitch")
        ops243.set_pitch_trigger_callback(_ops243_pitch_trigger)

    def set_threshold(value: float) -> None:
        if audio is not None:
            audio.set_threshold(value)
            log.info("Audio threshold → %.3f", audio.threshold)

    _calib_session: CalibSession | None = None

    def calib_start(height_mm: float, dist_mm: float) -> None:
        nonlocal _calib_session
        if _calib_session is not None:
            _calib_session.release()
        _calib_session = CalibSession(height_mm, dist_mm)
        log.info("Calibration session started (height=%.0f mm, dist=%.0f mm)", height_mm, dist_mm)

    def _do_capture() -> None:
        nonlocal _calib_session
        if _calib_session is None:
            return
        result = _calib_session.capture()
        server.broadcast({"type": "calib_result", **dataclasses.asdict(result)})
        if result.ok:
            log.info("Calibration saved: %s", result.message)
        else:
            log.warning("Calibration failed: %s", result.message)

    def calib_capture() -> None:
        loop.run_in_executor(None, _do_capture)

    def calib_stop() -> None:
        nonlocal _calib_session
        if _calib_session is not None:
            _calib_session.release()
            _calib_session = None
        log.info("Calibration session stopped")

    server.set_callbacks(arm=arm, disarm=disarm, reset=reset, set_threshold=set_threshold, set_mode=set_mode,
                         calib_start=calib_start, calib_capture=calib_capture,
                         calib_stop=calib_stop,
                         get_history=(store.load_recent if store is not None else None))

    log.info("Starting stereo capture …")
    try:
        capturer.start()
    except Exception as exc:
        log.warning("Stereo cameras not available (%s) — continuing without live capture", exc)

    if spin_cap is not None:
        if spin_cap.start():
            log.info("Spin camera started (index %d @ %d fps)",
                     config.SPIN_CAM_IDX, config.SPIN_CAM_FPS)
        else:
            log.warning("Spin camera not found at index %d — "
                        "falling back to stereo cam 0 for spin", config.SPIN_CAM_IDX)
            spin_cap = None

    log.info("Ready. Connect browser to ws://localhost:%d", config.WS_PORT)
    server.broadcast({"type": "status", "state": "idle"})

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(sig_name: str = "shutdown") -> None:
        log.info("Received %s — shutting down …", sig_name)
        loop.stop()

    # asyncio.loop.add_signal_handler is Unix-only; use signal.signal on Windows
    if sys.platform == "win32":
        signal.signal(signal.SIGINT,
                      lambda sig, frame: loop.call_soon_threadsafe(loop.stop))
    else:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: _shutdown(signal.Signals(s).name))

    async def _run() -> None:
        await asyncio.gather(
            server.serve(),
            server.stream_audio_levels(audio),
            server.stream_health(),
            server.stream_calib_frames(lambda: _calib_session),
        )

    try:
        loop.run_until_complete(_run())
    finally:
        capturer.stop()
        if spin_cap:
            spin_cap.stop()
        if audio:
            audio.stop()
        if ops243:
            ops243.stop()
        if radar:
            radar.stop()
        loop.close()
        log.info("Stopped.")


if __name__ == "__main__":
    main()
