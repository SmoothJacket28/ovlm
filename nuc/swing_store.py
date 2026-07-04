"""
Durable local swing storage — append-only JSONL on the machine running the
monitor (the Mac), one file per month.

Why this design is the most foolproof option available:

  * Append-only writes cannot corrupt existing data. There is no database
    page/index/WAL to corrupt, no schema to migrate, no daemon to crash.
    The only possible damage from a crash or power loss is a torn LAST
    line, which the reader detects (it simply fails to parse) and skips.
  * Every append is flushed AND fsync'd before the swing is reported, so
    an acknowledged swing is on disk even if the process dies the next
    millisecond. Swings arrive seconds apart — the fsync cost is nothing.
  * Each line is a self-contained JSON record with its own id and
    timestamp. Any tool can read the files (jq, pandas, a text editor),
    and a partial/corrupt line never affects neighbours.
  * Files rotate monthly (swings/2026-07.jsonl), so no file grows
    unboundedly and old months can be archived or gzipped by hand.

Space: records are compacted before writing — null fields dropped,
trajectory coordinates rounded to millimetres (3 decimals), compact JSON
separators. A swing with a 25-point trajectory is ~700 bytes; 100 swings
a day for a year is ~25 MB. Set SWING_TRAJECTORY_DECIMALS or trim the
trajectory if you need even less.

Storage location (SWING_STORE_DIR overrides):
  macOS:  ~/Library/Application Support/OVLM/swings/
  other:  ~/.ovlm/swings/
"""

import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def default_store_dir() -> Path:
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'OVLM' / 'swings'
    return Path.home() / '.ovlm' / 'swings'


def _compact(value: Any, decimals: int) -> Any:
    """Recursively drop null members and round floats for compact storage."""
    if isinstance(value, float):
        return round(value, decimals)
    if isinstance(value, dict):
        return {k: _compact(v, decimals) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_compact(v, decimals) for v in value]
    return value


class SwingStore:
    """Append-only JSONL store, one file per month, fsync per append."""

    def __init__(self, directory: Optional[str] = None,
                 trajectory_decimals: int = 3) -> None:
        self._dir = Path(directory) if directory else default_store_dir()
        self._decimals = trajectory_decimals
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def _file_for(self, epoch_s: float) -> Path:
        return self._dir / (time.strftime('%Y-%m', time.localtime(epoch_s)) + '.jsonl')

    # ── Write path ─────────────────────────────────────────────────────────────

    def append(self, payload: Dict[str, Any], kind: str = 'hit') -> Dict[str, Any]:
        """Persist one swing. Returns the stored record (payload + identity
        fields) AFTER it is physically on disk — broadcast that record so
        the dashboard and the archive agree on id and timestamp."""
        record = dict(payload)
        record.setdefault('id', uuid.uuid4().hex)
        record.setdefault('timestamp', int(time.time() * 1000))   # epoch ms
        record.setdefault('kind', kind)

        # Compact: strip nulls, round floats. Trajectory dominates size —
        # millimetre precision is far below triangulation noise anyway.
        record = _compact(record, self._decimals)

        line = json.dumps(record, separators=(',', ':')) + '\n'
        path = self._file_for(record['timestamp'] / 1000.0)
        with self._lock:
            # Open per append: always appends to the correct month file,
            # never holds a stale handle, and 'a' mode is atomic-position
            # on POSIX. fsync before returning = the swing is durable.
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        return record

    # ── Read path ──────────────────────────────────────────────────────────────

    def load_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Last `limit` swings across month files, oldest → newest.
        Tolerates torn/corrupt lines (skipped with a warning)."""
        records: List[Dict[str, Any]] = []
        for path in sorted(self._dir.glob('*.jsonl'), reverse=True):
            file_records = self._read_file(path)
            records = file_records + records
            if len(records) >= limit:
                break
        return records[-limit:]

    def count(self) -> int:
        return sum(len(self._read_file(p)) for p in self._dir.glob('*.jsonl'))

    def disk_usage_bytes(self) -> int:
        return sum(p.stat().st_size for p in self._dir.glob('*.jsonl'))

    def _read_file(self, path: Path) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            with open(path, encoding='utf-8') as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        # Torn line from a crash mid-append — data loss is
                        # exactly this one record, never the file.
                        log.warning("Skipping corrupt line %s:%d", path.name, n)
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError as exc:
            log.error("Cannot read %s: %s", path, exc)
        return out
