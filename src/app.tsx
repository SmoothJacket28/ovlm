import React, { useEffect } from 'react';
import { Dashboard } from './components/layout/Dashboard';
import { useStore } from './state/store';
import { piClient } from './ws/client';
import { initSimulatorBridge } from './integrations/simulator';
import { loadArchivedSwings, persistSwings } from './modules/storage/swing-archive';

export function App(): React.ReactElement {
  const wsHost              = useStore((s) => s.wsHost);
  const updatePipeline      = useStore((s) => s.updatePipelineStatus);
  const ingestMeasurement   = useStore((s) => s.ingestPiMeasurement);
  const updateAudioLevel    = useStore((s) => s.updateAudioLevel);
  const updatePiHealth      = useStore((s) => s.updatePiHealth);
  const setLastFrame        = useStore((s) => s.setLastFrame);
  const setCalibWizardResult = useStore((s) => s.setCalibWizardResult);
  const setWizardStep       = useStore((s) => s.setWizardStep);
  const mergeStoredSwings   = useStore((s) => s.mergeStoredSwings);
  const mergeSessions       = useStore((s) => s.mergeSessions);

  useEffect(() => {
    piClient.connect(wsHost, {
      updatePipelineStatus: updatePipeline as (p: Record<string, unknown>) => void,
      ingestPiMeasurement:  ingestMeasurement,
      updateAudioLevel,
      updatePiHealth,
      setLastFrame,
      setCalibWizardResult,
      setWizardStep,
      mergeStoredSwings,
    });
    return () => piClient.disconnect();
  }, [wsHost]);

  useEffect(() => {
    initSimulatorBridge(); // ready-handshake + auto-forward swings to the sim
  }, []);

  useEffect(() => {
    // Hydrate the session from the browser mirror (instant, works offline),
    // then keep the mirror current. The backend archive replays on connect;
    // ids dedupe the two sources.
    loadArchivedSwings().then((archived) => {
      if (archived.length) mergeSessions(archived);
    });
    let last = useStore.getState().swings;
    return useStore.subscribe((state) => {
      if (state.swings !== last) {
        last = state.swings;
        persistSwings(state.swings);
      }
    });
  }, []);

  return (
    <>
      <style>{globalStyles}</style>
      <Dashboard />
    </>
  );
}

const globalStyles = `
  @keyframes spin {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
  }

  @keyframes record-flash {
    0%   { box-shadow: 0 0 0 2px #ff6644, 0 0 18px #ff6644; border-color: #ff6644; color: #ff6644; }
    60%  { box-shadow: 0 0 0 1px #ff6644, 0 0 8px #ff6644;  border-color: #ff6644; color: #ff6644; }
    100% { box-shadow: none; border-color: #111; color: inherit; }
  }

  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: #080810; }
  ::-webkit-scrollbar-thumb { background: #1a1a2e; border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: #2a2a4e; }

  button:hover { filter: brightness(1.15); }
  button:active { filter: brightness(0.9); }
`;
