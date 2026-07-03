import React from 'react';
import { useStore, useSwings, useActiveSwing, useSessionMode } from '@/state/store';
import type { SwingSession } from '@/types/pipeline';
import type { BallMeasurement } from '@/types/tracking';
import type { LiveMetricsView, SessionMode } from '@/state/sessionMode.slice';
import { EVChart } from './EVChart';
import { SprayChart } from './SprayChart';
import { StrikeZoneChart } from './StrikeZoneChart';
import {
  trueSpinRateRpm, gyroDegree, spinClockLabel, calculatedDistanceFt, apexHeightFt,
} from '@/modules/metrics/derived';
import {
  openSimulator, sendSwingToSimulator, getAutoSend, setAutoSend,
} from '@/integrations/simulator';

function exportCsv(swings: SwingSession[]): void {
  const header = [
    'swing', 'timestamp', 'exit_velocity_mph', 'launch_angle_deg', 'spray_angle_deg',
    'calculated_distance_ft', 'apex_height_ft', 'pitch_velocity_mph',
    'total_spin_rpm', 'true_spin_rpm', 'spin_efficiency_pct', 'gyro_degree', 'spin_axis_clock',
    'vertical_break_in', 'horizontal_break_in', 'ssw_break_v_in', 'ssw_break_h_in',
    'release_height_ft', 'release_side_ft', 'extension_ft',
    'plate_loc_x_ft', 'plate_loc_y_ft',
    'detect_rate_pct', 'latency_ms',
  ].join(',');

  const rows = [...swings].reverse().map((sw, i) => {
    const b = sw.ball;
    return [
      i + 1,
      new Date(sw.timestamp).toISOString(),
      b.exitVelocity,
      b.launchAngle,
      b.sprayAngle,
      calculatedDistanceFt(b).toFixed(1),
      apexHeightFt(b)?.toFixed(1) ?? '',
      b.pitchVelocityMph ?? '',
      b.seam?.spinRate ?? '',
      b.seam ? trueSpinRateRpm(b.seam).toFixed(0) : '',
      b.seam != null ? (b.seam.spinEfficiency * 100).toFixed(1) : '',
      b.seam ? gyroDegree(b.seam).toFixed(1) : '',
      b.seam ? spinClockLabel(b.seam.spinAxis) : '',
      b.verticalBreakIn ?? '',
      b.horizontalBreakIn ?? '',
      b.sswBreakVIn ?? '',
      b.sswBreakHIn ?? '',
      b.releaseHeightFt ?? '',
      b.releaseSideFt ?? '',
      b.extensionFt ?? '',
      b.plateLocation?.xFt ?? '',
      b.plateLocation?.yFt ?? '',
      (b.detectRate * 100).toFixed(1),
      b.processingLatencyMs.toFixed(0),
    ].join(',');
  });

  const csv  = [header, ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `ovlm_session_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Mode-driven metric set ──────────────────────────────────────────────────────
// Which metric cards appear depends on the active session mode: Pitching cares
// about the inbound radar reading + movement profile, Hitting about the batted
// ball, Live tracks both (filterable via the ALL/PITCHING/HITTING toggle).

type MetricKey =
  | 'exitVelocity' | 'launchAngle' | 'sprayAngle' | 'calculatedDistance' | 'apexHeight'
  | 'spinRate' | 'trueSpinRate' | 'spinEfficiency' | 'gyroDegree' | 'spinAxis'
  | 'verticalBreak' | 'horizontalBreak' | 'sswBreakV' | 'sswBreakH'
  | 'releaseHeight' | 'releaseSide' | 'extension'
  | 'pitchVelocity' | 'detectRate' | 'latency';

const METRIC_META: Record<MetricKey, { label: string; unit: string }> = {
  exitVelocity:       { label: 'EXIT VELOCITY',     unit: 'mph' },
  launchAngle:        { label: 'LAUNCH ANGLE',      unit: '°' },
  sprayAngle:         { label: 'DIRECTION',         unit: '°' },
  calculatedDistance: { label: 'CALCULATED DIST.',  unit: 'ft' },
  apexHeight:         { label: 'APEX HEIGHT',       unit: 'ft' },
  spinRate:           { label: 'TOTAL SPIN RATE',   unit: 'rpm' },
  trueSpinRate:       { label: 'TRUE SPIN RATE',    unit: 'rpm' },
  spinEfficiency:     { label: 'SPIN EFFICIENCY',   unit: '%' },
  gyroDegree:         { label: 'GYRO DEGREE',       unit: '°' },
  spinAxis:           { label: 'SPIN DIRECTION',    unit: 'clock' },
  verticalBreak:      { label: 'VERTICAL BREAK',    unit: 'in' },
  horizontalBreak:    { label: 'HORIZONTAL BREAK',  unit: 'in' },
  sswBreakV:          { label: 'SSW BREAK (V)',     unit: 'in' },
  sswBreakH:          { label: 'SSW BREAK (H)',     unit: 'in' },
  releaseHeight:      { label: 'RELEASE HEIGHT',    unit: 'ft' },
  releaseSide:        { label: 'RELEASE SIDE',      unit: 'ft' },
  extension:          { label: 'EXTENSION',         unit: 'ft' },
  pitchVelocity:      { label: 'PITCH VELOCITY',    unit: 'mph' },
  detectRate:         { label: 'DETECT RATE',       unit: '%' },
  latency:            { label: 'LATENCY',           unit: 'ms' },
};

const PITCHING_METRICS: MetricKey[] = [
  'pitchVelocity', 'spinRate', 'trueSpinRate', 'spinEfficiency', 'gyroDegree', 'spinAxis',
  'verticalBreak', 'horizontalBreak', 'sswBreakV', 'sswBreakH',
  'releaseHeight', 'releaseSide', 'extension',
];
const HITTING_METRICS: MetricKey[] = [
  'exitVelocity', 'launchAngle', 'sprayAngle', 'calculatedDistance', 'apexHeight',
  'spinRate', 'spinAxis', 'pitchVelocity',
];
const SHARED_METRICS: MetricKey[] = ['detectRate', 'latency'];

function metricsForMode(mode: SessionMode | null, liveView: LiveMetricsView): MetricKey[] {
  switch (mode) {
    case 'pitching': return [...PITCHING_METRICS, ...SHARED_METRICS];
    case 'hitting':  return [...HITTING_METRICS, ...SHARED_METRICS];
    case 'live':
      if (liveView === 'pitching') return [...PITCHING_METRICS, ...SHARED_METRICS];
      if (liveView === 'hitting')  return [...HITTING_METRICS, ...SHARED_METRICS];
      return [...PITCHING_METRICS, ...HITTING_METRICS, ...SHARED_METRICS];
    default: return [...HITTING_METRICS, ...SHARED_METRICS];
  }
}

const MODE_LABELS: Record<SessionMode, string> = {
  pitching: 'PITCHING', hitting: 'HITTING', live: 'LIVE',
};

const MPH_TO_KPH = 1.60934;

function fmtEv(ev: number, unit: 'mph' | 'kph'): string {
  return (unit === 'kph' ? ev * MPH_TO_KPH : ev).toFixed(1);
}

/** One metric card for `key`. `ball` is null in the pre-session preview, where every card shows a dash. */
function renderMetricCard(
  key: MetricKey,
  ball: BallMeasurement | null,
  evUnit: 'mph' | 'kph',
  laRange: [number, number],
): React.ReactElement {
  const meta = METRIC_META[key];
  if (!ball) {
    return <MetricCard key={key} label={meta.label} unit={meta.unit} value="—" color="#334" />;
  }
  switch (key) {
    case 'exitVelocity':
      return <MetricCard key={key} label={meta.label} value={fmtEv(ball.exitVelocity, evUnit)} unit={evUnit}
        color="#ff6644" note={evNote(ball.exitVelocity)}
        badge={ball.evSource === 'radar' ? 'RADAR' : undefined} badgeColor="#44aaff" />;
    case 'launchAngle':
      return <MetricCard key={key} label={meta.label} value={`${ball.launchAngle}`} unit="°"
        color="#44aaff" note={laNote(ball.launchAngle, laRange[0], laRange[1])} />;
    case 'sprayAngle':
      return <MetricCard key={key} label={meta.label} value={`${ball.sprayAngle}`} unit="°"
        color="#44ff88" note={saNote(ball.sprayAngle)} />;
    case 'calculatedDistance':
      return <MetricCard key={key} label={meta.label} value={`${Math.round(calculatedDistanceFt(ball))}`} unit="ft"
        color="#66ddaa" badge={ball.carryDistanceM != null ? 'RADAR' : 'EST.'} badgeColor="#44aaff" />;
    case 'apexHeight': {
      const apex = apexHeightFt(ball);
      return apex != null
        ? <MetricCard key={key} label={meta.label} value={apex.toFixed(1)} unit="ft" color="#66aaff" />
        : <MetricCard key={key} label={meta.label} value="—" unit="ft" color="#334" />;
    }
    case 'spinRate':
      return ball.seam
        ? <MetricCard key={key} label={meta.label} value={ball.seam.spinRate.toLocaleString()} unit="rpm" color="#aa88ff" />
        : <MetricCard key={key} label={meta.label} value="—" unit="rpm" color="#334" />;
    case 'trueSpinRate':
      return ball.seam
        ? <MetricCard key={key} label={meta.label} value={Math.round(trueSpinRateRpm(ball.seam)).toLocaleString()} unit="rpm" color="#cc88ff" />
        : <MetricCard key={key} label={meta.label} value="—" unit="rpm" color="#334" />;
    case 'gyroDegree':
      return ball.seam
        ? <MetricCard key={key} label={meta.label} value={gyroDegree(ball.seam).toFixed(0)} unit="°" color="#ffaa88" />
        : <MetricCard key={key} label={meta.label} value="—" unit="°" color="#334" />;
    case 'spinAxis':
      return ball.seam
        ? <MetricCard key={key} label={meta.label} value={spinClockLabel(ball.seam.spinAxis)} unit="" color="#88aaff" />
        : <MetricCard key={key} label={meta.label} value="—" unit="clock" color="#334" />;
    case 'spinEfficiency':
      return ball.seam
        ? <MetricCard key={key} label={meta.label} value={`${(ball.seam.spinEfficiency * 100).toFixed(0)}`} unit="%" color="#ffaa44" />
        : <MetricCard key={key} label={meta.label} value="—" unit="%" color="#334" />;
    case 'verticalBreak':
      return ball.verticalBreakIn != null
        ? <MetricCard key={key} label={meta.label} value={ball.verticalBreakIn.toFixed(1)} unit="in" color="#44ccaa" />
        : <MetricCard key={key} label={meta.label} value="—" unit="in" color="#334" />;
    case 'horizontalBreak':
      return ball.horizontalBreakIn != null
        ? <MetricCard key={key} label={meta.label} value={ball.horizontalBreakIn.toFixed(1)} unit="in" color="#44ccaa" />
        : <MetricCard key={key} label={meta.label} value="—" unit="in" color="#334" />;
    case 'sswBreakV':
      return ball.sswBreakVIn != null
        ? <MetricCard key={key} label={meta.label} value={ball.sswBreakVIn.toFixed(1)} unit="in" color="#66bbcc" />
        : <MetricCard key={key} label={meta.label} value="—" unit="in" color="#334" />;
    case 'sswBreakH':
      return ball.sswBreakHIn != null
        ? <MetricCard key={key} label={meta.label} value={ball.sswBreakHIn.toFixed(1)} unit="in" color="#66bbcc" />
        : <MetricCard key={key} label={meta.label} value="—" unit="in" color="#334" />;
    case 'releaseHeight':
      return ball.releaseHeightFt != null
        ? <MetricCard key={key} label={meta.label} value={ball.releaseHeightFt.toFixed(1)} unit="ft" color="#aabb66" />
        : <MetricCard key={key} label={meta.label} value="—" unit="ft" color="#334" />;
    case 'releaseSide':
      return ball.releaseSideFt != null
        ? <MetricCard key={key} label={meta.label} value={ball.releaseSideFt.toFixed(1)} unit="ft" color="#aabb66" />
        : <MetricCard key={key} label={meta.label} value="—" unit="ft" color="#334" />;
    case 'extension':
      return ball.extensionFt != null
        ? <MetricCard key={key} label={meta.label} value={ball.extensionFt.toFixed(1)} unit="ft" color="#aabb66" />
        : <MetricCard key={key} label={meta.label} value="—" unit="ft" color="#334" />;
    case 'pitchVelocity':
      return ball.pitchVelocityMph != null
        ? <MetricCard key={key} label={meta.label} value={`${ball.pitchVelocityMph}`} unit="mph" color="#44ddff" />
        : <MetricCard key={key} label={meta.label} value="—" unit="mph" color="#334" />;
    case 'detectRate':
      return <MetricCard key={key} label={meta.label} value={`${(ball.detectRate * 100).toFixed(0)}`} unit="%"
        color={ball.detectRate >= 0.7 ? '#44ff88' : ball.detectRate >= 0.4 ? '#ffaa00' : '#ff4455'}
        note={ball.detectRate < 0.4 ? 'Low — check exposure' : undefined} />;
    case 'latency':
      return <MetricCard key={key} label={meta.label} value={`${ball.processingLatencyMs.toFixed(0)}`} unit="ms"
        color={ball.processingLatencyMs > 400 ? '#ff4455' : '#44ff88'} />;
  }
}

function LiveViewToggle({ value, onChange }: { value: LiveMetricsView; onChange: (v: LiveMetricsView) => void }) {
  const options: { value: LiveMetricsView; label: string }[] = [
    { value: 'all', label: 'ALL' },
    { value: 'pitching', label: 'PITCHING' },
    { value: 'hitting', label: 'HITTING' },
  ];
  return (
    <div style={styles.liveToggle}>
      {options.map((opt) => (
        <button
          key={opt.value}
          style={{ ...styles.liveToggleBtn, ...(opt.value === value ? styles.liveToggleBtnActive : {}) }}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function MetricsDashboard(): React.ReactElement {
  const swings = useSwings();
  const activeSwing = useActiveSwing();
  const setActiveSwing = useStore((s) => s.setActiveSwing);
  const aggregates = useStore((s) => s.aggregates);
  const selectedIds = useStore((s) => s.selectedSwingIds);
  const toggleSwing = useStore((s) => s.toggleSwingSelection);
  const recompute = useStore((s) => s.recomputeAggregates);
  const settings = useStore((s) => s.settings);
  const { evUnit, laOptimalMin, laOptimalMax } = settings;
  const sessionMode = useSessionMode();
  const liveMetricsView = useStore((s) => s.liveMetricsView);
  const setLiveMetricsView = useStore((s) => s.setLiveMetricsView);
  const [autoSend, setAutoSendState] = React.useState(getAutoSend());

  const metricKeys = metricsForMode(sessionMode, liveMetricsView);
  const laRange: [number, number] = [laOptimalMin, laOptimalMax];

  const handleToggle = (id: string) => {
    toggleSwing(id);
    recompute();
  };

  if (swings.length === 0) {
    return (
      <div style={styles.pending}>
        <div style={styles.pendingHeader}>
          <div style={{ fontSize: 28 }}>⚾</div>
          <div style={{ fontSize: 13, color: '#445', marginTop: 8 }}>No swings recorded yet.</div>
          <div style={{ fontSize: 11, color: '#334', marginTop: 4 }}>
            Switch to Capture, arm the trigger, and take some cuts.
          </div>
        </div>

        <div style={styles.pendingSection}>
          <div style={styles.pendingSectionHeaderRow}>
            <div style={styles.sidebarHeader}>
              METRICS RECORDED THIS SESSION{sessionMode && ` — ${MODE_LABELS[sessionMode]}`}
            </div>
            {sessionMode === 'live' && <LiveViewToggle value={liveMetricsView} onChange={setLiveMetricsView} />}
          </div>
          <div style={styles.metricsGrid}>
            {metricKeys.map((key) => renderMetricCard(key, null, evUnit, laRange))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.root}>
      {/* EV history chart — full width across the top */}
      <EVChart />

      {/* Swing list + detail — fills remaining height */}
      <div style={styles.body}>
      {/* Swing list sidebar */}
      <div style={styles.sidebar}>
        <div style={styles.sidebarHeader}>SWINGS ({swings.length})</div>
        <div style={styles.swingList}>
          {swings.map((sw, i) => (
            <div
              key={sw.id}
              style={{
                ...styles.swingRow,
                ...(sw.id === activeSwing?.id ? styles.swingRowActive : {}),
              }}
              onClick={() => setActiveSwing(sw.id)}
            >
              <input
                type="checkbox"
                checked={selectedIds.has(sw.id)}
                onChange={() => handleToggle(sw.id)}
                onClick={(e) => e.stopPropagation()}
                style={{ margin: 0 }}
              />
              <div style={styles.swingIndex}>#{swings.length - i}</div>
              <div style={styles.swingMeta}>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#cc8844', fontVariantNumeric: 'tabular-nums' }}>
                  {fmtEv(sw.ball.exitVelocity, evUnit)} <span style={{ fontSize: 9, color: '#667' }}>{evUnit}</span>
                </div>
                <div style={{ fontSize: 9, color: '#556' }}>
                  LA: {sw.ball.launchAngle}° | {new Date(sw.timestamp).toLocaleTimeString()}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Session aggregates */}
        {selectedIds.size > 0 && (
          <div style={styles.aggregatePanel}>
            <div style={styles.sidebarHeader}>SELECTED AVG ({aggregates.sessionCount})</div>
            <AggRow label="Exit Vel"   value={`${fmtEv(aggregates.avgExitVelocity, evUnit)} ${evUnit}`} />
            <AggRow label="Max EV"     value={`${fmtEv(aggregates.maxExitVelocity, evUnit)} ${evUnit}`} />
            <AggRow label="Launch ∠"   value={`${aggregates.avgLaunchAngle.toFixed(1)}°`} />
            <AggRow label="Spin Rate"  value={`${aggregates.avgSpinRate.toFixed(0)} rpm`} />
          </div>
        )}

        {/* Export + simulator */}
        <div style={styles.exportRow}>
          <button style={styles.exportBtn} onClick={() => exportCsv(swings)}>
            ↓ Export CSV
          </button>
          <button
            style={{ ...styles.exportBtn, marginTop: 6, color: '#cc8844', borderColor: '#2a1f10' }}
            title="Replay the selected swing's ball flight in the MLB stadium simulator"
            onClick={() => (activeSwing ? sendSwingToSimulator(activeSwing.ball) : openSimulator())}
          >
            ⚾ {activeSwing ? 'Replay in Simulator' : 'Open Simulator'}
          </button>
          <label style={styles.autoSendRow}>
            <input
              type="checkbox"
              checked={autoSend}
              onChange={(e) => { setAutoSend(e.target.checked); setAutoSendState(e.target.checked); }}
              style={{ margin: 0 }}
            />
            Auto-send new swings
          </label>
        </div>
      </div>

      {/* Detail panel for active swing */}
      {activeSwing ? (
        <div style={styles.detail}>
          {sessionMode === 'live' && (
            <div style={styles.detailHeaderRow}>
              <LiveViewToggle value={liveMetricsView} onChange={setLiveMetricsView} />
            </div>
          )}

          {/* Ball metrics */}
          <div style={styles.metricsGrid}>
            {metricKeys.map((key) => renderMetricCard(key, activeSwing.ball, evUnit, laRange))}
          </div>

          {/* Spray chart (batted ball) / strike zone (pitch location), per mode */}
          {(sessionMode === 'pitching' || (sessionMode === 'live' && liveMetricsView !== 'hitting')) && (
            <StrikeZoneChart location={activeSwing.ball.plateLocation} />
          )}
          {(sessionMode === 'hitting' || (sessionMode === 'live' && liveMetricsView !== 'pitching')) && (
            <SprayChart />
          )}
        </div>
      ) : (
        <div style={styles.selectPrompt}>← Select a swing to view details</div>
      )}
      </div>  {/* end body */}
    </div>
  );
}

function MetricCard({ label, value, unit, color, note, badge, badgeColor }: {
  label: string; value: string; unit: string; color: string;
  note?: string; badge?: string; badgeColor?: string;
}) {
  return (
    <div style={styles.card}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={styles.cardLabel}>{label}</div>
        {badge && (
          <div style={{
            fontSize: 8, fontWeight: 700, letterSpacing: '0.1em',
            color: badgeColor ?? '#445',
            border: `1px solid ${badgeColor ?? '#445'}`,
            borderRadius: 3, padding: '1px 4px', opacity: 0.8,
          }}>
            {badge}
          </div>
        )}
      </div>
      <div style={{ ...styles.cardValue, color }}>
        {value}<span style={styles.cardUnit}> {unit}</span>
      </div>
      {note && <div style={styles.cardNote}>{note}</div>}
    </div>
  );
}

function AggRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.aggRow}>
      <span style={styles.aggLabel}>{label}</span>
      <span style={styles.aggValue}>{value}</span>
    </div>
  );
}

function evNote(ev: number): string {
  if (ev >= 110) return 'Elite power';
  if (ev >= 100) return 'Above average';
  if (ev >= 90)  return 'Average MLB';
  return 'Below average';
}
function laNote(la: number, min = 8, max = 32): string {
  if (la >= min && la <= max) return 'Optimal range';
  if (la < 0)                 return 'Ground ball';
  if (la > 40)                return 'Pop up';
  return '';
}
function saNote(sa: number): string {
  if (Math.abs(sa) < 10)  return 'Center field';
  if (sa > 15)            return 'Pull side';
  if (sa < -15)           return 'Oppo field';
  return '';
}

const styles: Record<string, React.CSSProperties> = {
  root: { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' },
  body: { display: 'flex', flex: 1, overflow: 'hidden' },
  pending: {
    height: '100%', overflow: 'auto', padding: 24, display: 'flex',
    flexDirection: 'column', gap: 20,
  },
  pendingHeader: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center',
    padding: '12px 0',
  },
  pendingSection: { display: 'flex', flexDirection: 'column', gap: 10 },
  pendingSectionHeaderRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between' },
  detailHeaderRow: { display: 'flex', justifyContent: 'flex-end' },
  liveToggle: {
    display: 'flex', border: '1px solid #1a1a2e', borderRadius: 4, overflow: 'hidden', width: 'fit-content',
  },
  liveToggleBtn: {
    padding: '4px 10px', background: 'transparent', border: 'none', borderRight: '1px solid #1a1a2e',
    color: '#445', cursor: 'pointer', fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', fontFamily: 'inherit',
  },
  liveToggleBtnActive: { background: '#131326', color: '#4488ff' },
  sidebar: {
    width: 200, minWidth: 200, borderRight: '1px solid #1a1a2e', display: 'flex',
    flexDirection: 'column', overflow: 'hidden',
  },
  sidebarHeader: {
    padding: '10px 12px 6px', fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
    color: '#445', borderBottom: '1px solid #111',
  },
  swingList: { flex: 1, overflowY: 'auto' },
  swingRow: {
    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', cursor: 'pointer',
    borderBottom: '1px solid #0d0d18', transition: 'background 0.1s',
  },
  swingRowActive: { background: '#0d0d20' },
  swingIndex: { fontSize: 9, color: '#334', width: 20, textAlign: 'right', flexShrink: 0 },
  swingMeta: { flex: 1, minWidth: 0 },
  aggregatePanel: { borderTop: '1px solid #1a1a2e', padding: '8px 0' },
  aggRow: { display: 'flex', justifyContent: 'space-between', padding: '3px 12px' },
  aggLabel: { fontSize: 10, color: '#445' },
  aggValue: { fontSize: 10, color: '#99aacc', fontVariantNumeric: 'tabular-nums' },
  exportRow: { padding: '8px 10px', borderTop: '1px solid #111', marginTop: 'auto' },
  autoSendRow: {
    display: 'flex', alignItems: 'center', gap: 6, marginTop: 6,
    fontSize: 9, color: '#556', cursor: 'pointer', userSelect: 'none',
  },
  exportBtn: {
    width: '100%', padding: '5px 0', background: 'transparent',
    border: '1px solid #1a2a1a', borderRadius: 4, color: '#445',
    fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', cursor: 'pointer',
    fontFamily: 'inherit',
  },
  detail: { flex: 1, overflow: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 20 },
  selectPrompt: {
    flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 12, color: '#334',
  },
  metricsGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12 },
  card: {
    background: '#0d0d18', border: '1px solid #1a1a2e', borderRadius: 8,
    padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 4,
  },
  cardLabel: { fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', color: '#445' },
  cardValue: { fontSize: 26, fontWeight: 700, lineHeight: 1, fontVariantNumeric: 'tabular-nums' },
  cardUnit: { fontSize: 11, opacity: 0.6 },
  cardNote: { fontSize: 9, color: '#556', marginTop: 2 },
};
