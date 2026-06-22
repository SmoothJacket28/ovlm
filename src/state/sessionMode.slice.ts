import type { StateCreator } from 'zustand';

export type SessionMode = 'pitching' | 'hitting' | 'live';
export type LiveDelivery = 'machine' | 'live-arm';
export type LiveScenario = 'bullpen' | 'game';
export type LiveMetricsView = 'all' | 'pitching' | 'hitting';

export interface SessionModeSlice {
  /** null until the user picks a mode and starts a session */
  sessionMode: SessionMode | null;
  liveDelivery: LiveDelivery;
  liveScenario: LiveScenario;
  /** Which metric set the dashboard shows during a 'live' session */
  liveMetricsView: LiveMetricsView;

  startSession: (mode: SessionMode, opts?: { liveDelivery?: LiveDelivery; liveScenario?: LiveScenario }) => void;
  endSession: () => void;
  setLiveMetricsView: (view: LiveMetricsView) => void;
}

export const createSessionModeSlice: StateCreator<SessionModeSlice> = (set) => ({
  sessionMode: null,
  liveDelivery: 'live-arm',
  liveScenario: 'game',
  liveMetricsView: 'all',

  startSession: (mode, opts) =>
    set({
      sessionMode: mode,
      liveDelivery: opts?.liveDelivery ?? 'live-arm',
      liveScenario: opts?.liveScenario ?? 'game',
      liveMetricsView: 'all',
    }),

  endSession: () => set({ sessionMode: null }),

  setLiveMetricsView: (view) => set({ liveMetricsView: view }),
});
