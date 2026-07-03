# OVLM — Open-Vision Launch Monitor

A camera-based baseball launch monitor: stereo high-speed cameras measure exit
velocity, launch angle, spray, and spin, and a TrackMan-style dashboard shows
live metrics, session history, and a 3-D MLB stadium ball-flight simulator.

Two parts:

| Part | Where it runs | What it does |
|------|---------------|--------------|
| `nuc/` capture pipeline (Python) | The machine the cameras are plugged into (Mac, Windows, or Linux) | Captures frames, detects and triangulates the ball, serves measurements over WebSocket (`ws://localhost:8765`) |
| Web dashboard (this repo root) | Any modern browser | Live metrics, calibration wizard, session tracking, 3-D stadium view — works fully offline (all assets vendored) |

## Quick start — macOS

### 1. Capture pipeline

```bash
cd nuc
chmod +x install.sh
./install.sh
```

The installer uses Homebrew for `portaudio` (needed by the mic bat-crack
trigger), creates a virtualenv at `~/ovlm-venv`, installs Python dependencies,
and can register a launchd service that starts OVLM at login.

> **macOS permissions:** the first run prompts for **Camera** and
> **Microphone** access. Grant both in *System Settings → Privacy & Security*
> or the cameras and audio trigger will not open.

Then calibrate (once per camera setup) and start:

```bash
~/ovlm-venv/bin/python plate_calib.py --live   # home plate in view, no board
~/ovlm-venv/bin/python main.py
```

### 2. Dashboard

```bash
npm install
npm run dev        # http://localhost:5173
```

Open the dashboard, keep the default `ws://localhost:8765` (or enter
`ws://<capture-machine-ip>:8765` if the cameras are on another machine), and
click **CONNECT**.

For a production build: `npm run build`, then serve `dist/` with any static
file server (`npm run preview` to test it).

## Quick start — Windows

```powershell
cd nuc
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

Dashboard steps are identical to macOS.

## Hardware

- 2× global-shutter USB cameras (OV9281-class, e.g. QILOVE 800P) for stereo
  tracking at 640×480 @ 210 fps
- Optional third camera for seam-based spin measurement
- Optional microphone for the bat-crack trigger
- See `nuc/config.py` for all tunables and `nuc/CALIBRATION.md` for the
  calibration guide

## Notes

- **Exposure on macOS:** AVFoundation ignores manual exposure on most external
  UVC cameras, so auto-exposure stays on. Keep the hitting area brightly lit
  and verify with `python camera_check.py --preview` that the ball isn't
  smearing.
- The 3-D stadium simulator (`public/baseball_simulator.html`) and all of its
  dependencies (three.js, Tailwind CSS, DRACO decoder, 30 stadium models) are
  served same-origin — no internet connection is needed at runtime.
