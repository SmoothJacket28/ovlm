/**
 * Browser-side swing persistence (IndexedDB via idb-keyval).
 *
 * The authoritative archive is the append-only JSONL store on the machine
 * running the monitor (nuc/swing_store.py) — this mirror only makes the
 * DASHBOARD resilient: a page refresh, browser crash, or offline restart
 * restores the session instantly, even before (or without) a backend
 * connection. Swings carry the backend-assigned id, so hydration, live
 * broadcasts, and backend history replays dedupe cleanly against each other.
 *
 * Space: capped at the newest MAX_SWINGS sessions; IndexedDB stores the
 * structured objects directly (no JSON string overhead).
 */

import { get, set } from 'idb-keyval';
import type { SwingSession } from '@/types/pipeline';

const KEY = 'ovlm_swing_archive_v1';
const MAX_SWINGS = 1000;
const SAVE_DEBOUNCE_MS = 500;

export async function loadArchivedSwings(): Promise<SwingSession[]> {
  try {
    const stored = await get<SwingSession[]>(KEY);
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];   // private-mode / quota / corrupt value — never block boot
  }
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;

/** Debounced write of the newest MAX_SWINGS sessions. */
export function persistSwings(swings: SwingSession[]): void {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    set(KEY, swings.slice(0, MAX_SWINGS)).catch(() => {
      /* quota exceeded / private mode — the backend archive still has everything */
    });
  }, SAVE_DEBOUNCE_MS);
}
