# OPS243 radar accuracy guide

OVLM uses an OmniPreSense OPS243-C-FC-RP (Doppler + FMCW) for pitch velocity,
exit velocity, and carry range. The driver (`ops243.py`) configures the sensor
and corrects its two systematic error sources automatically; this page covers
what it does and the two things **you** must do for best accuracy: mount it
right and enter the mount position.

## What the software does

1. **20 ksps sampling (`S2`).** The factory default (10 ksps) cannot measure
   above 69.5 mph — real pitches and batted balls were invisible or wrong.
   At 20 ksps the ceiling is 139.1 mph with ~0.27 mph resolution.
2. **Peak-of-event speeds.** A ball decelerates from drag while the radar
   keeps reporting, so "latest reading" systematically underreads. The driver
   buffers readings and reports the event peak (with spike rejection), which
   matches the release-speed / contact-speed conventions used by TrackMan.
3. **Cosine-error correction.** Doppler measures only the radial component
   `v·cos(θ)`. The pipeline recovers true speed by dividing by `cos(θ)`
   computed from the camera-fitted trajectory and the antenna position
   (`OPS243_POS_M` in `config.py`).
4. **Magnitude gating.** Weak returns (multipath ghosts, edge-of-beam
   clutter) are rejected below `OPS243_MIN_MAGNITUDE`.
5. **On-device filtering.** Sub-30 mph objects (people, bat waggle, fans)
   are dropped by the sensor itself (`R>` filter) before they reach OVLM.

## Mounting for accuracy

- Mount the antenna **directly behind home plate**, centered on the
  pitcher–plate line, facing the pitcher. Every degree off-axis costs
  `1 − cos(θ)` of raw reading; the software corrects it, but corrections
  amplify noise, so start as close to head-on as possible.
- Height: near ball-flight height (0.3–1 m works; batted-ball launch and
  pitch descent both stay within a few degrees of the beam axis there).
- **Measure the antenna position and set `OPS243_POS_M`** in `config.py`
  (meters, plate frame: x lateral, y up, z toward the pitcher; behind the
  plate is negative z). The cosine correction is only as good as this value.
- Nothing metal moving in the beam (fans, pitching machines directly behind
  the hitter's path) — or raise `OPS243_MIN_MAGNITUDE` until ghosts stop.

## Verifying calibration

- **Tuning fork:** OmniPreSense documents speed verification with a tuning
  fork (AN-026). A fork stamped e.g. "1,570 Hz" reads as a fixed reference
  speed — strike it and hold it in front of the antenna; the reported speed
  must match the fork's rating within ±0.5%. This validates the Doppler chain
  end-to-end and needs no moving ball.
- **Cross-sensor:** with the cameras calibrated, `main.py` logs every
  radar/camera pair (`OPS243 EV … camera=…`). Sustained deltas beyond ~3%
  after cosine correction usually mean `OPS243_POS_M` is wrong.
- Run `python main.py -v` to see raw readings with magnitudes while tuning.

## Tunables (config.py)

| Setting | Default | Meaning |
|---|---|---|
| `OPS243_SAMPLE_RATE_CMD` | `'S2'` | 20 ksps → 139.1 mph max |
| `OPS243_MIN_MAGNITUDE` | `10.0` | reject returns weaker than this |
| `OPS243_EVENT_WINDOW_S` | `1.2` | readings grouped into one event |
| `OPS243_FRESH_S` | `2.5` | readings older than this are stale |
| `OPS243_POS_M` | `(0.0, 0.3, -1.0)` | antenna position, plate frame |
| `OPS243_EV_EVAL_DIST_M` | `3.0` | flight distance where peak EV reading occurs |
| `OPS243_COS_FLOOR` | `0.5` | below this cos(θ), skip correction, camera wins |
| `OPS243_PITCH_COS_DEFAULT` | `0.995` | static pitch correction w/o camera fit |
| `OPS243_AGREE_FRACTION` | `0.15` | radar/camera agreement gate |

Driver tests: `cd nuc && python -m unittest tests.test_ops243 -v`
