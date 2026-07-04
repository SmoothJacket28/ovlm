import type { StateCreator } from 'zustand';
import type { SwingSession, PipelineStatus } from '@/types/pipeline';
import type { BallMeasurement } from '@/types/tracking';
import type { PiMessage, StoredSwing } from '@/ws/messages';

type MeasurementMsg = Extract<PiMessage, { type: 'measurement' }>;

export interface AudioLevel {
  rms: number;
  peak: number;
  threshold: number;
}

export interface PiHealth {
  cpuTempC:   number;
  memUsedMb:  number;
  memTotalMb: number;
  loadAvg1m:  number;
}

export interface SessionSlice {
  swings: SwingSession[];
  activeSwingId: string | null;
  pipelineStatus: PipelineStatus;
  wsHost: string;
  audioLevel: AudioLevel | null;
  piHealth:   PiHealth | null;
  allTimeBestEv:   number;
  newRecordSwingId: string | null;   // set for ~3s when a swing beats the all-time record

  addSwing: (session: SwingSession) => void;
  setActiveSwing: (id: string | null) => void;
  updatePipelineStatus: (patch: Partial<PipelineStatus>) => void;
  ingestPiMeasurement: (msg: MeasurementMsg) => void;
  mergeStoredSwings: (stored: StoredSwing[]) => void;
  mergeSessions: (sessions: SwingSession[]) => void;
  setWsHost: (host: string) => void;
  clearSwings: () => void;
  updateAudioLevel: (level: AudioLevel) => void;
  updatePiHealth:   (health: PiHealth) => void;
  clearNewRecord: () => void;
}

const defaultPipelineStatus: PipelineStatus = {
  state: 'idle',
  wsConnected: false,
  latencyMs: 0,
  audioArmed: false,
};

const STORAGE_KEY_HOST   = 'ovlm_pi_host';
const STORAGE_KEY_ATR    = 'ovlm_all_time_ev';   // all-time record EV
// Watermark set by clearSwings: archived swings at or before this epoch-ms
// stay hidden from restores (the durable archive itself is never touched).
const STORAGE_KEY_CLEARED = 'ovlm_cleared_before';

// Backend runs on this machine since the NUC port — migrate the stale
// Raspberry Pi default if it's what's saved, but leave custom hosts alone.
const DEFAULT_WS_HOST = 'ws://localhost:8765';
function loadWsHost(): string {
  const saved = localStorage.getItem(STORAGE_KEY_HOST);
  if (saved == null || saved === 'ws://raspberrypi.local:8765') return DEFAULT_WS_HOST;
  return saved;
}

export const createSessionSlice: StateCreator<SessionSlice> = (set) => ({
  swings: [],
  activeSwingId: null,
  pipelineStatus: defaultPipelineStatus,
  wsHost: loadWsHost(),
  audioLevel: null,
  piHealth:   null,
  allTimeBestEv:    parseFloat(localStorage.getItem(STORAGE_KEY_ATR) ?? '0') || 0,
  newRecordSwingId: null,

  addSwing: (session) =>
    set((s) => {
      const ev = session.ball.exitVelocity;
      const isRecord = ev > s.allTimeBestEv;
      if (isRecord) localStorage.setItem(STORAGE_KEY_ATR, String(ev));
      return {
        swings: [session, ...s.swings],
        activeSwingId: session.id,
        ...(isRecord && { allTimeBestEv: ev, newRecordSwingId: session.id }),
      };
    }),

  setActiveSwing: (id) => set({ activeSwingId: id }),

  updatePipelineStatus: (patch) =>
    set((s) => ({
      pipelineStatus: { ...s.pipelineStatus, ...patch },
    })),

  ingestPiMeasurement: (msg) =>
    set((s) => {
      // Dedupe: history replay and live broadcast can carry the same swing
      // (the backend store assigns the id both agree on).
      if (msg.id && s.swings.some((sw) => sw.id === msg.id)) return {};
      const session = measurementToSession(msg);
      const isRecord = session.ball.exitVelocity > s.allTimeBestEv;
      if (isRecord) {
        localStorage.setItem(STORAGE_KEY_ATR, String(session.ball.exitVelocity));
      }
      return {
        swings: [session, ...s.swings],
        activeSwingId: session.id,
        pipelineStatus: {
          ...s.pipelineStatus,
          latencyMs: msg.latencyMs,
          state: 'armed',
        },
        ...(isRecord && {
          allTimeBestEv:    session.ball.exitVelocity,
          newRecordSwingId: session.id,
        }),
      };
    }),

  // Backend history replay: raw measurement-shaped records → sessions.
  mergeStoredSwings: (stored) =>
    set((s) => mergeSessionsInto(s, stored.map((m) => measurementToSession(m)))),

  // IndexedDB hydration: already-mapped SwingSession objects.
  mergeSessions: (sessions) =>
    set((s) => mergeSessionsInto(s, sessions)),

  setWsHost: (host) => {
    localStorage.setItem(STORAGE_KEY_HOST, host);
    set({ wsHost: host });
  },

  clearSwings: () => {
    // Clearing hides history, it does not delete it: mark the moment so
    // archive restores (backend replay / IndexedDB hydration) don't
    // resurrect the swings the user just dismissed.
    localStorage.setItem(STORAGE_KEY_CLEARED, String(Date.now()));
    set({ swings: [], activeSwingId: null });
  },

  updateAudioLevel: (level)  => set({ audioLevel: level }),
  updatePiHealth:   (health) => set({ piHealth: health }),
  clearNewRecord:   ()       => set({ newRecordSwingId: null }),
});

/** Merge restored swings (backend history replay or IndexedDB hydration)
 *  without live-swing side effects: no arming, no record flash — but the
 *  all-time best still absorbs archived EVs so a cleared localStorage
 *  can't shrink the record. Dedupes by id against current swings. */
function mergeSessionsInto(
  s: Pick<SessionSlice, 'swings' | 'allTimeBestEv' | 'activeSwingId'>,
  incoming: SwingSession[],
): Partial<SessionSlice> {
  const clearedBefore = parseFloat(localStorage.getItem(STORAGE_KEY_CLEARED) ?? '0') || 0;
  const known = new Set(s.swings.map((sw) => sw.id));
  const restored = incoming.filter(
    (sw) => !known.has(sw.id) && sw.timestamp > clearedBefore);
  if (restored.length === 0) return {};
  const swings = [...s.swings, ...restored]
    .sort((a, b) => b.timestamp - a.timestamp);
  const bestEv = Math.max(s.allTimeBestEv, ...restored.map((r) => r.ball.exitVelocity));
  if (bestEv > s.allTimeBestEv) {
    localStorage.setItem(STORAGE_KEY_ATR, String(bestEv));
  }
  return {
    swings,
    allTimeBestEv: bestEv,
    activeSwingId: s.activeSwingId ?? swings[0]?.id ?? null,
  };
}

/** Build a SwingSession from a measurement/stored-swing payload. Stored
 *  records keep their archive id + timestamp so dedupe works across
 *  live broadcasts, history replays, and IndexedDB hydration. */
export function measurementToSession(
  msg: Omit<MeasurementMsg, 'type'> & { type?: string },
): SwingSession {
  const ball: BallMeasurement = {
    exitVelocity: msg.exitVelocity,
    launchAngle: msg.launchAngle,
    sprayAngle: msg.sprayAngle,
    seam: msg.spin
      ? {
          spinRate: msg.spin.rpm,
          spinAxis: msg.spin.axis,
          spinEfficiency: msg.spin.efficiency,
          seamFrames: [],
          confidence: msg.spin.confidence,
        }
      : null,
    contactFrameIndex: 0,
    processingLatencyMs: msg.latencyMs,
    detectRate: msg.detectRate,
    evSource: msg.evSource,
    radarVelocityMph: msg.radarVelocityMps != null
      ? Math.round(msg.radarVelocityMps * 2.23694 * 10) / 10
      : null,
    pitchVelocityMph: msg.pitchVelocity ?? null,
    carryDistanceM:   msg.carryDistanceM ?? null,
    verticalBreakIn:   msg.verticalBreakIn ?? null,
    horizontalBreakIn: msg.horizontalBreakIn ?? null,
    sswBreakVIn:       msg.sswBreakVIn ?? null,
    sswBreakHIn:       msg.sswBreakHIn ?? null,
    releaseHeightFt:   msg.releaseHeightFt ?? null,
    releaseSideFt:     msg.releaseSideFt ?? null,
    extensionFt:       msg.extensionFt ?? null,
    plateLocation:     msg.plateLocation ?? null,
    trajectory: (msg.trajectory ?? []).map((p) => ({
      x: p.x, y: p.y, z: p.z,
      timestamp: p.t * 1_000_000,
    })),
  };
  return {
    id: msg.id ?? crypto.randomUUID(),
    timestamp: msg.timestamp ?? Date.now(),
    ball,
    hasReplayFrames: false,
  };
}
