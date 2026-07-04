/** One measured swing. The backend's durable store strips null fields
 *  before broadcast, so nullable fields are also optional. `id` and
 *  `timestamp` (epoch ms) are assigned by the store — the dashboard uses
 *  the id to dedupe history replays against live swings. */
export interface MeasurementMessage {
  type: 'measurement';
  id?: string;
  timestamp?: number;      // epoch ms, assigned when persisted
  kind?: 'hit' | 'pitch';
  exitVelocity: number;    // mph
  launchAngle: number;     // degrees
  sprayAngle: number;      // degrees
  fitResidualMm: number;
  latencyMs: number;
  detectRate: number;
  /** Inlier / rejected point counts from the trajectory fit (newer backends) */
  pointsUsed?: number;
  pointsRejected?: number;
  spin?: {
    rpm: number;
    axis: [number, number, number];
    efficiency: number;
    confidence: number;
    framesUsed: number;
    /** Which camera measured spin: dedicated high-fps cam or stereo cam 0 */
    source?: 'spincam' | 'stereo';
  };
  evSource: 'camera' | 'radar';
  radarVelocityMps?: number | null;
  pitchVelocity?:    number | null;   // mph — OPS243 inbound reading (pitch speed)
  carryDistanceM?:   number | null;   // metres — OPS243 FMCW range at peak distance
  trajectory?: Array<{ x: number; y: number; z: number; t: number }>;
  // Pitch movement / release — null until pre-contact pitch-trajectory tracking exists
  verticalBreakIn?:   number | null;
  horizontalBreakIn?: number | null;
  sswBreakVIn?:       number | null;
  sswBreakHIn?:       number | null;
  releaseHeightFt?:   number | null;
  releaseSideFt?:     number | null;
  extensionFt?:       number | null;
  plateLocation?:     { xFt: number; yFt: number } | null;
}

/** A swing record replayed from the backend's durable archive — the same
 *  shape as a measurement, with id/timestamp guaranteed present. */
export type StoredSwing = Omit<MeasurementMessage, 'id' | 'timestamp'> & {
  id: string;
  timestamp: number;
};

/** Messages the launch-monitor backend sends to the browser */
export type PiMessage =
  | {
      type: 'status';
      state: 'idle' | 'armed' | 'capturing' | 'processing';
      audioArmed?: boolean;
    }
  | MeasurementMessage
  | { type: 'history'; swings: StoredSwing[] }
      /** Inlier / rejected point counts from the trajectory fit (newer backends) */
  | { type: 'audio_level'; rms: number; peak: number; threshold: number }
  | { type: 'health'; cpuTempC: number; memUsedMb: number; memTotalMb: number; loadAvg1m: number }
  | { type: 'error'; message: string }
  | { type: 'calib_frame'; cam0: string; cam1: string;
      cornersFound: [boolean, boolean]; step: 'align' | 'capturing' | 'done' }
  | { type: 'calib_result'; ok: boolean; reprojPx: number;
      residualMm: number; baselineMm: number; message: string };

/** Messages the browser sends to the launch-monitor backend */
export type BrowserMessage =
  | { type: 'arm' }
  | { type: 'disarm' }
  | { type: 'reset' }
  | { type: 'set_threshold'; value: number }
  | { type: 'set_mode'; mode: 'pitching' | 'hitting' | 'live' }
  | { type: 'calib_start'; heightMm: number; distanceMm: number }
  | { type: 'calib_capture' }
  | { type: 'calib_stop' }
  | { type: 'get_history'; limit?: number };
