#!/usr/bin/env bash
# OVLM installer — macOS (and Linux)
# Run once:
#   chmod +x install.sh && ./install.sh
#
# What it does:
#   1. Installs system dependencies (portaudio, needed to build pyaudio)
#   2. Creates a Python virtualenv at ~/ovlm-venv
#   3. Installs Python dependencies into the venv
#   4. (macOS) Registers a launchd agent that starts OVLM at login
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$HOME/ovlm-venv"
OS="$(uname -s)"

echo "=== OVLM installer ==="
echo "Project dir : $SCRIPT_DIR"
echo "Virtualenv  : $VENV_DIR"
echo ""

# ── 1. System dependencies ────────────────────────────────────────────────────
echo "[1/4] Checking system dependencies ..."
if [[ "$OS" == "Darwin" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
        echo "Homebrew not found. Install it first: https://brew.sh" >&2
        exit 1
    fi
    # portaudio is required to compile the pyaudio wheel
    brew list portaudio >/dev/null 2>&1 || brew install portaudio
else
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y portaudio19-dev python3-venv python3-dev
    else
        echo "  (non-apt Linux — install portaudio dev headers with your package manager)"
    fi
fi

# ── 2. Python ─────────────────────────────────────────────────────────────────
echo ""
echo "[2/4] Checking Python ..."
PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
    echo "python3 not found. Install Python 3.10+ (macOS: brew install python)" >&2
    exit 1
fi
echo "  Found: $("$PYTHON" --version)"

# ── 3. Virtualenv + dependencies ──────────────────────────────────────────────
echo ""
echo "[3/4] Setting up virtualenv at $VENV_DIR ..."
[[ -d "$VENV_DIR" ]] || "$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" --quiet
echo "  Installed packages:"
"$VENV_DIR/bin/pip" list --format=columns | grep -Ei "opencv|numpy|websockets|pyaudio|pyserial|psutil" || true

# ── 4. Start-at-login service (macOS launchd) ─────────────────────────────────
echo ""
if [[ "$OS" == "Darwin" ]]; then
    echo "[4/4] Registering launchd agent com.ovlm.monitor ..."
    PLIST="$HOME/Library/LaunchAgents/com.ovlm.monitor.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.ovlm.monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python</string>
        <string>$SCRIPT_DIR/main.py</string>
    </array>
    <key>WorkingDirectory</key><string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict><key>SuccessfulExit</key><false/></dict>
    <key>StandardOutPath</key><string>$HOME/Library/Logs/ovlm.log</string>
    <key>StandardErrorPath</key><string>$HOME/Library/Logs/ovlm.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    echo "  Wrote $PLIST (not started yet)"
else
    echo "[4/4] Skipping start-at-login service (macOS only). Run manually:"
    echo "  $VENV_DIR/bin/python $SCRIPT_DIR/main.py"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Run directly in a terminal:"
echo "  cd '$SCRIPT_DIR'"
echo "  '$VENV_DIR/bin/python' main.py"
echo ""
if [[ "$OS" == "Darwin" ]]; then
    echo "Manage the login service:"
    echo "  launchctl load  ~/Library/LaunchAgents/com.ovlm.monitor.plist   # enable + start"
    echo "  launchctl unload ~/Library/LaunchAgents/com.ovlm.monitor.plist  # stop + disable"
    echo "  tail -f ~/Library/Logs/ovlm.log                                 # logs"
    echo ""
    echo "macOS will prompt for Camera and Microphone permission the first time"
    echo "OVLM runs — grant both in System Settings > Privacy & Security, or the"
    echo "cameras/audio trigger will not open."
    echo ""
fi
echo "IMPORTANT: Run calibration before starting. Either:"
echo "  '$VENV_DIR/bin/python' plate_calib.py --live   # home plate in view, no board"
echo "  '$VENV_DIR/bin/python' calibrate.py  --live    # ChArUco board"
echo ""
echo "Connect the browser dashboard to: ws://localhost:8765"
echo "(or ws://<host-ip>:8765 from another machine on the same network)"
echo ""

if [[ "$OS" == "Darwin" ]]; then
    read -r -p "Start the service now? [y/N] " answer
    if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
        launchctl load "$HOME/Library/LaunchAgents/com.ovlm.monitor.plist"
        echo "Service started. Logs: tail -f ~/Library/Logs/ovlm.log"
    fi
fi
