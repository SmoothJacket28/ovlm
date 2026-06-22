import React, { useState, useCallback } from 'react';
import { useStore, usePipelineStatus, useWsHost, useSessionMode } from '@/state/store';
import { piClient } from '@/ws/client';
import type { BallMeasurement } from '@/types/tracking';
import type { AudioLevel } from '@/state/session.slice';
import type { SessionMode, LiveDelivery, LiveScenario } from '@/state/sessionMode.slice';

const MODE_META: Record<SessionMode, { label: string; icon: string; hint: string }> = {
  pitching: { label: 'PITCHING', icon: '🎯', hint: 'Trigger: radar release detect. Tracks pitch velocity, spin, and movement.' },
  hitting:  { label: 'HITTING',  icon: '🏏', hint: 'Trigger: mic bat-crack. Tracks exit velocity, launch angle, and spray.' },
  live:     { label: 'LIVE',     icon: '⚡', hint: 'Trigger: mic + radar. Tracks both pitch and batted-ball metrics.' },
};

export function DualVideoPanel(): React.ReactElement {
  const status    = usePipelineStatus();
  const wsHost    = useWsHost();
  const setWsHost = useStore((s) => s.setWsHost);
  const swings    = useStore((s) => s.swings);

  const sessionMode  = useSessionMode();
  const liveDelivery = useStore((s) => s.liveDelivery);
  const liveScenario = useStore((s) => s.liveScenario);
  const startSession  = useStore((s) => s.startSession);
  const endSession    = useStore((s) => s.endSession);

  const audioLevel = useStore((s) => s.audioLevel);
  const [hostDraft, setHostDraft] = useState(wsHost);

  const lastBall: BallMeasurement | null = swings[0]?.ball ?? null;

  const applyHost = useCallback(() => {
    const url = hostDraft.trim();
    if (!url) return;
    setWsHost(url);
    piClient.disconnect();
    // App's useEffect re-fires on wsHost change and reconnects
  }, [hostDraft, setWsHost]);

  const arm    = () => piClient.send({ type: 'arm' });
  const disarm = () => piClient.send({ type: 'disarm' });
  const reset  = () => piClient.send({ type: 'reset' });

  const handleStartSession = (mode: SessionMode, opts?: { liveDelivery: LiveDelivery; liveScenario: LiveScenario }) => {
    startSession(mode, opts);
    piClient.send({ type: 'set_mode', mode });
  };

  const handleEndSession = () => {
    piClient.send({ type: 'disarm' });
    endSession();
  };

  const triggerTitle = sessionMode === 'pitching' ? 'RADAR TRIGGER'
    : sessionMode === 'live' ? 'MIC + RADAR TRIGGER'
    : 'MIC TRIGGER';
  const showAudioMeter = sessionMode !== 'pitching';

  const connected = status.wsConnected;
  const armed     = status.audioArmed;

  return (
    <div style={styles.root}>
      {/* ── Connection card ────────────────────────────────────── */}
      <div style={styles.card}>
        <div style={styles.cardTitle}>MONITOR CONNECTION</div>

        <div style={styles.hostRow}>
          <input
            style={styles.hostInput}
            value={hostDraft}
            onChange={(e) => setHostDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && applyHost()}
            placeholder="ws://localhost:8765"
            spellCheck={false}
          />
          <button style={styles.btnSecondary} onClick={applyHost}>
            CONNECT
          </button>
        </div>

        {/* WS status */}
        <div style={styles.wsStatus}>
          <div style={{
            ...styles.dot,
            background: connected ? '#44ff88' : '#ff4455',
            boxShadow: connected ? '0 0 6px #44ff88' : '0 0 6px #ff4455',
          }} />
          <span style={{ color: connected ? '#44ff88' : '#ff4455', fontSize: 11, letterSpacing: '0.08em' }}>
            {connected ? 'CONNECTED' : 'DISCONNECTED'}
          </span>
          <span style={{ fontSize: 10, color: '#445', marginLeft: 4 }}>{wsHost}</span>
        </div>

        {status.errorMessage && (
          <div style={styles.errorMsg}>{status.errorMessage}</div>
        )}
      </div>

      {/* ── Session mode: pick one to unlock capture, or show the active one ── */}
      {sessionMode === null ? (
        <SessionStartCard onStart={handleStartSession} />
      ) : (
        <>
          <div style={styles.card}>
            <div style={styles.sessionBadgeRow}>
              <div>
                <div style={styles.cardTitle}>ACTIVE SESSION</div>
                <div style={styles.sessionModeValue}>
                  {MODE_META[sessionMode].icon} {MODE_META[sessionMode].label}
                  {sessionMode === 'live' && (
                    <span style={styles.sessionSubBadge}>
                      {liveDelivery === 'machine' ? 'MACHINE' : 'LIVE ARM'} · {liveScenario === 'bullpen' ? 'BULLPEN' : 'GAME'}
                    </span>
                  )}
                </div>
              </div>
              <button style={styles.btnSecondary} onClick={handleEndSession}>END SESSION</button>
            </div>
          </div>

          {/* ── Trigger controls ────────────────────────────────────── */}
          <div style={styles.card}>
            <div style={styles.cardTitle}>{triggerTitle}</div>
            <div style={styles.triggerRow}>
              <button
                style={{ ...styles.btnPrimary, opacity: !connected || armed ? 0.4 : 1 }}
                disabled={!connected || armed}
                onClick={arm}
              >
                🎙 ARM
              </button>
              <button
                style={{ ...styles.btnDanger, opacity: !connected || !armed ? 0.4 : 1 }}
                disabled={!connected || !armed}
                onClick={disarm}
              >
                ⬛ DISARM
              </button>
              <button
                style={{ ...styles.btnSecondary, opacity: !connected ? 0.4 : 1 }}
                disabled={!connected}
                onClick={reset}
              >
                ↺ RESET
              </button>
            </div>

            <div style={styles.armedIndicator}>
              <div style={{
                ...styles.dot,
                background: armed ? '#ff4455' : '#223',
                boxShadow: armed ? '0 0 8px #ff4455' : 'none',
                width: 10, height: 10,
                transition: 'all 0.2s',
              }} />
              <span style={{ fontSize: 11, color: armed ? '#ff4455' : '#445', letterSpacing: '0.12em' }}>
                {armed ? 'ARMED — WAITING FOR SWING' : 'NOT ARMED'}
              </span>
            </div>
          </div>

          {/* ── Audio level meter ───────────────────────────────────── */}
          {showAudioMeter && audioLevel && connected && (
            <AudioLevelCard level={audioLevel} />
          )}

          {/* ── Pipeline state ───────────────────────────────────────── */}
          <div style={styles.card}>
            <div style={styles.cardTitle}>PIPELINE</div>
            <PipelineStates state={status.state} latencyMs={status.latencyMs} />
          </div>

          {/* ── Last measurement ─────────────────────────────────────── */}
          {lastBall && (
            <div style={styles.card}>
              <div style={styles.cardTitle}>LAST SWING</div>
              <div style={styles.metricsRow}>
                <Metric label="EXIT VEL"  value={`${lastBall.exitVelocity}`}                        unit="mph" color="#ff6644" />
                <Metric label="LAUNCH ∠"  value={`${lastBall.launchAngle}`}                         unit="°"   color="#44aaff" />
                <Metric label="SPRAY ∠"   value={`${lastBall.sprayAngle}`}                          unit="°"   color="#44ff88" />
                <Metric label="DETECT"    value={`${(lastBall.detectRate * 100).toFixed(0)}`}       unit="%"
                  color={lastBall.detectRate >= 0.7 ? '#44ff88' : lastBall.detectRate >= 0.4 ? '#ffaa00' : '#ff4455'} />
                <Metric label="LATENCY"   value={`${lastBall.processingLatencyMs.toFixed(0)}`}      unit="ms"  color="#aaaacc" />
              </div>
            </div>
          )}
        </>
      )}

      {/* ── Setup guide (shown when disconnected) ────────────────── */}
      {!connected && (
        <div style={styles.guide}>
          <div style={styles.guideTitle}>GETTING STARTED</div>
          <ol style={styles.guideList}>
            <li>In the <code style={styles.code}>nuc\</code> folder run: <code style={styles.code}>python main.py</code></li>
            <li>Enter the monitor's WebSocket URL above (default ws://localhost:8765)</li>
            <li>Click CONNECT — the status dot will turn green</li>
            <li>If not yet calibrated, run <code style={styles.code}>python plate_calib.py --live</code> (see the Calibrate tab)</li>
            <li>Pick a session mode below and click START SESSION</li>
            <li>Click ARM, then go</li>
          </ol>
        </div>
      )}
    </div>
  );
}

// ── Session start picker ─────────────────────────────────────────────────────
// Choosing a mode here is what starts a session: it tells the backend which
// trigger source(s) to arm and the Metrics dashboard which fields to show.

function SessionStartCard({ onStart }: {
  onStart: (mode: SessionMode, opts?: { liveDelivery: LiveDelivery; liveScenario: LiveScenario }) => void;
}) {
  const [mode, setMode] = useState<SessionMode>('hitting');
  const [delivery, setDelivery] = useState<LiveDelivery>('live-arm');
  const [scenario, setScenario] = useState<LiveScenario>('game');

  return (
    <div style={styles.card}>
      <div style={styles.cardTitle}>START SESSION</div>

      <div style={styles.modeRow}>
        {(Object.keys(MODE_META) as SessionMode[]).map((m) => (
          <button
            key={m}
            style={{ ...styles.modeBtn, ...(mode === m ? styles.modeBtnActive : {}) }}
            onClick={() => setMode(m)}
          >
            <span style={{ fontSize: 16 }}>{MODE_META[m].icon}</span>
            {MODE_META[m].label}
          </button>
        ))}
      </div>
      <div style={styles.modeHint}>{MODE_META[mode].hint}</div>

      {mode === 'live' && (
        <div style={styles.liveSubRow}>
          <div style={styles.subGroup}>
            <div style={styles.subGroupLabel}>DELIVERY</div>
            <SubToggle
              options={[{ value: 'live-arm', label: 'LIVE ARM' }, { value: 'machine', label: 'MACHINE' }]}
              value={delivery}
              onChange={(v) => setDelivery(v as LiveDelivery)}
            />
          </div>
          <div style={styles.subGroup}>
            <div style={styles.subGroupLabel}>SCENARIO</div>
            <SubToggle
              options={[{ value: 'game', label: 'GAME' }, { value: 'bullpen', label: 'BULLPEN' }]}
              value={scenario}
              onChange={(v) => setScenario(v as LiveScenario)}
            />
          </div>
        </div>
      )}

      <button
        style={{ ...styles.btnPrimary, marginTop: 4, alignSelf: 'flex-start' }}
        onClick={() => onStart(mode, mode === 'live' ? { liveDelivery: delivery, liveScenario: scenario } : undefined)}
      >
        ▶ START SESSION
      </button>
    </div>
  );
}

function SubToggle({ options, value, onChange }: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div style={styles.subToggle}>
      {options.map((opt) => (
        <button
          key={opt.value}
          style={{ ...styles.subToggleBtn, ...(opt.value === value ? styles.subToggleBtnActive : {}) }}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ── Audio level meter ──────────────────────────────────────────────────────────
const METER_MAX = 0.7;   // RMS values above this are clipped to bar edge

function AudioLevelCard({ level }: { level: AudioLevel }): React.ReactElement {
  const [localThreshold, setLocalThreshold] = useState(level.threshold);

  // Keep local slider in sync when Pi sends a new threshold (e.g. after reconnect)
  const displayed = localThreshold;

  const rmsW   = Math.min(1, level.rms  / METER_MAX) * 100;
  const peakW  = Math.min(1, level.peak / METER_MAX) * 100;
  const threshW = Math.min(1, displayed / METER_MAX) * 100;

  // RMS bar colour: green → amber → red as it approaches threshold
  const ratio = level.rms / displayed;
  const barColor =
    ratio >= 1.0 ? '#44ff88' :   // triggered — bright green
    ratio >= 0.7 ? '#ffaa00' :   // getting close — amber
    '#446644';                    // quiet — dark green

  const handleSlider = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseFloat(e.target.value);
    setLocalThreshold(v);
    piClient.send({ type: 'set_threshold', value: v });
  };

  return (
    <div style={styles.card}>
      <div style={styles.cardTitle}>MIC LEVEL — THRESHOLD CALIBRATION</div>

      {/* VU bar */}
      <div style={vuStyles.barWrap}>
        {/* RMS fill */}
        <div style={{ ...vuStyles.barFill, width: `${rmsW}%`, background: barColor, transition: 'width 0.08s linear' }} />
        {/* Peak hold tick */}
        <div style={{ ...vuStyles.peakTick, left: `${peakW}%` }} />
        {/* Threshold line */}
        <div style={{ ...vuStyles.threshLine, left: `${threshW}%` }}>
          <div style={vuStyles.threshLabel}>{displayed.toFixed(2)}</div>
        </div>
      </div>

      {/* Numeric readout */}
      <div style={vuStyles.readout}>
        <span style={{ color: '#44aaff' }}>RMS <b>{level.rms.toFixed(3)}</b></span>
        <span style={{ color: '#88aacc' }}>PEAK <b>{level.peak.toFixed(3)}</b></span>
        <span style={{ color: '#ffaa44' }}>THRESHOLD <b>{displayed.toFixed(3)}</b></span>
      </div>

      {/* Threshold slider */}
      <div style={vuStyles.sliderRow}>
        <span style={vuStyles.sliderLabel}>0</span>
        <input
          type="range"
          min="0.01"
          max="0.70"
          step="0.01"
          value={displayed}
          onChange={handleSlider}
          style={vuStyles.slider}
        />
        <span style={vuStyles.sliderLabel}>0.70</span>
      </div>

      <div style={{ fontSize: 9, color: '#334', lineHeight: 1.4 }}>
        Drag the slider so the orange line sits above background noise and below the bat-crack peak.
        Changes apply instantly — no restart needed.
      </div>
    </div>
  );
}

const vuStyles: Record<string, React.CSSProperties> = {
  barWrap: {
    position: 'relative',
    height: 20,
    background: '#07070f',
    border: '1px solid #1a1a2e',
    borderRadius: 3,
    overflow: 'visible',
  },
  barFill: {
    position: 'absolute',
    top: 0, left: 0, height: '100%',
    borderRadius: 2,
  },
  peakTick: {
    position: 'absolute',
    top: 0, bottom: 0,
    width: 2,
    background: '#88aacc',
    transform: 'translateX(-1px)',
  },
  threshLine: {
    position: 'absolute',
    top: -4, bottom: -4,
    width: 2,
    background: '#ffaa44',
    transform: 'translateX(-1px)',
  },
  threshLabel: {
    position: 'absolute',
    top: -14,
    left: '50%',
    transform: 'translateX(-50%)',
    fontSize: 8,
    color: '#ffaa44',
    whiteSpace: 'nowrap',
    fontVariantNumeric: 'tabular-nums',
  },
  readout: {
    display: 'flex',
    gap: 16,
    fontSize: 10,
    fontVariantNumeric: 'tabular-nums',
    color: '#556',
  },
  sliderRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  sliderLabel: {
    fontSize: 9,
    color: '#445',
    width: 24,
    textAlign: 'center',
  },
  slider: {
    flex: 1,
    accentColor: '#ffaa44',
  },
};

const PIPELINE_STEPS = ['idle', 'armed', 'capturing', 'processing'] as const;
const STEP_LABELS: Record<string, string> = {
  idle: 'Idle', armed: 'Armed', capturing: 'Capturing', processing: 'Processing',
};

function PipelineStates({ state, latencyMs }: { state: string; latencyMs: number }) {
  const activeIdx = PIPELINE_STEPS.indexOf(state as typeof PIPELINE_STEPS[number]);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
      {PIPELINE_STEPS.map((step, i) => {
        const active  = step === state;
        const done    = i < activeIdx;
        const color   = active ? '#44aaff' : done ? '#44ff88' : '#1a1a2e';
        const txtColor = active ? '#44aaff' : done ? '#44ff88' : '#334';
        return (
          <React.Fragment key={step}>
            <div style={{ textAlign: 'center' }}>
              <div style={{
                width: 10, height: 10, borderRadius: '50%',
                background: color, margin: '0 auto 4px',
                boxShadow: active ? `0 0 8px ${color}` : 'none',
                transition: 'all 0.2s',
              }} />
              <div style={{ fontSize: 8, color: txtColor, letterSpacing: '0.08em' }}>
                {STEP_LABELS[step]}
              </div>
            </div>
            {i < PIPELINE_STEPS.length - 1 && (
              <div style={{
                flex: 1, height: 1,
                background: done ? '#44ff88' : '#1a1a2e',
                margin: '-8px 6px 0',
                transition: 'background 0.2s',
              }} />
            )}
          </React.Fragment>
        );
      })}
      {latencyMs > 0 && (
        <div style={{ marginLeft: 16, fontSize: 10, color: '#556' }}>
          {latencyMs.toFixed(0)} ms
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, unit, color }: { label: string; value: string; unit: string; color: string }) {
  return (
    <div style={{ textAlign: 'center', minWidth: 80 }}>
      <div style={{ fontSize: 9, color: '#445', letterSpacing: '0.12em', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color, fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>
        {value}
      </div>
      <div style={{ fontSize: 10, color: '#556', marginTop: 1 }}>{unit}</div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    padding: 20,
    height: '100%',
    overflowY: 'auto',
    boxSizing: 'border-box',
  },
  card: {
    background: '#0d0d18',
    border: '1px solid #1a1a2e',
    borderRadius: 8,
    padding: '14px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  cardTitle: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.15em',
    color: '#445',
    marginBottom: 2,
  },
  hostRow: {
    display: 'flex',
    gap: 8,
  },
  hostInput: {
    flex: 1,
    background: '#07070f',
    border: '1px solid #1a1a2e',
    borderRadius: 4,
    color: '#aabbcc',
    fontFamily: 'inherit',
    fontSize: 12,
    padding: '5px 10px',
    outline: 'none',
  },
  wsStatus: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    display: 'inline-block',
    flexShrink: 0,
  },
  sessionBadgeRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  sessionModeValue: {
    fontSize: 16,
    fontWeight: 700,
    color: '#aabbdd',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginTop: 2,
  },
  sessionSubBadge: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.08em',
    color: '#667788',
    border: '1px solid #1a1a2e',
    borderRadius: 4,
    padding: '2px 6px',
  },
  modeRow: {
    display: 'flex',
    gap: 8,
  },
  modeBtn: {
    flex: 1,
    padding: '10px 8px',
    background: '#07070f',
    border: '1px solid #1a1a2e',
    borderRadius: 6,
    color: '#667788',
    cursor: 'pointer',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.08em',
    fontFamily: 'inherit',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 4,
  },
  modeBtnActive: {
    background: '#131326',
    color: '#4488ff',
    border: '1px solid #1a3a6e',
  },
  modeHint: {
    fontSize: 9,
    color: '#445',
    lineHeight: 1.4,
  },
  liveSubRow: {
    display: 'flex',
    gap: 16,
    paddingTop: 4,
  },
  subGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  subGroupLabel: {
    fontSize: 8,
    color: '#445',
    letterSpacing: '0.1em',
  },
  subToggle: {
    display: 'flex',
    border: '1px solid #1a1a2e',
    borderRadius: 4,
    overflow: 'hidden',
    width: 'fit-content',
  },
  subToggleBtn: {
    padding: '4px 10px',
    background: 'transparent',
    border: 'none',
    borderRight: '1px solid #1a1a2e',
    color: '#445',
    cursor: 'pointer',
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.08em',
    fontFamily: 'inherit',
  },
  subToggleBtnActive: {
    background: '#131326',
    color: '#4488ff',
  },
  errorMsg: {
    fontSize: 10,
    color: '#ff4455',
    background: '#1a0010',
    border: '1px solid #441020',
    borderRadius: 4,
    padding: '4px 8px',
  },
  triggerRow: {
    display: 'flex',
    gap: 8,
  },
  armedIndicator: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  metricsRow: {
    display: 'flex',
    gap: 24,
    justifyContent: 'center',
    paddingTop: 4,
  },
  btnPrimary: {
    padding: '7px 16px',
    background: '#002244',
    color: '#66aaff',
    border: '1px solid #1a3a6e',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.08em',
    fontFamily: 'inherit',
  },
  btnDanger: {
    padding: '7px 16px',
    background: '#220010',
    color: '#ff6688',
    border: '1px solid #441030',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.08em',
    fontFamily: 'inherit',
  },
  btnSecondary: {
    padding: '7px 14px',
    background: '#0d0d14',
    color: '#667788',
    border: '1px solid #1a1a2e',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.08em',
    fontFamily: 'inherit',
  },
  guide: {
    background: '#07070f',
    border: '1px solid #111',
    borderRadius: 8,
    padding: '14px 16px',
  },
  guideTitle: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.15em',
    color: '#334',
    marginBottom: 8,
  },
  guideList: {
    margin: 0,
    paddingLeft: 18,
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    fontSize: 11,
    color: '#445',
    lineHeight: 1.5,
  },
  code: {
    background: '#111',
    border: '1px solid #1a1a2e',
    borderRadius: 3,
    padding: '1px 5px',
    fontFamily: 'inherit',
    color: '#88aaff',
    fontSize: 11,
  },
};
