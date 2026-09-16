from __future__ import annotations

import os
from datetime import datetime, timezone

from midnight_ghost.ingest.events import Event

MAX_OPEN_HANDLES = 100


def partition_key(service: str, timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    minute = dt.strftime('%Y-%m-%dT%H:%M')
    return f'{service}/{minute}.jsonl'


class PartitionedWriter:
    def __init__(self, base_path: str) -> None:
        self.base_path = base_path
        self._handles: dict[str, object] = {}
        self._counts: dict[str, int] = {}
        self.total_written = 0

    def write(self, event: Event) -> None:
        key = partition_key(event.service, event.timestamp_ns)
        fh = self._get_handle(key)
        fh.write(event.to_json() + '\n')
        self._counts[key] = self._counts.get(key, 0) + 1
        self.total_written += 1

    def _get_handle(self, key: str):
        if key in self._handles:
            return self._handles[key]
        if len(self._handles) >= MAX_OPEN_HANDLES:
            self._evict_handles()
        path = os.path.join(self.base_path, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, 'a')
        self._handles[key] = fh
        return fh

    def _evict_handles(self) -> None:
        keys = list(self._handles.keys())
        for k in keys[:len(keys) // 2]:
            self._handles[k].close()
            del self._handles[k]

    def close(self) -> None:
        for fh in self._handles.values():
            fh.close()
        self._handles.clear()

    def get_stats(self) -> dict:
        service_counts: dict[str, int] = {}
        for key, count in self._counts.items():
            svc = key.split('/')[0]
            service_counts[svc] = service_counts.get(svc, 0) + count
        return {
            'total_events': self.total_written,
            'partitions': len(self._counts),
            'per_service': service_counts,
        }
