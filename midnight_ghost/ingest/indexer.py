from __future__ import annotations

import hashlib
import json
import os
import struct
from collections import defaultdict
from datetime import datetime, timezone

from midnight_ghost.ingest.events import Event


def _minute_key(timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M')


class IndexBuilder:
    def __init__(self, event_store_path: str, index_path: str) -> None:
        self.event_store_path = event_store_path
        self.index_path = index_path

    def build_all(self) -> dict:
        stats = {
            'templates': 0,
            'severity_entries': 0,
            'traces': 0,
            'metric_series': 0,
            'bloom_filters': 0,
        }

        template_ts: dict[str, dict[tuple[str, str], int]] = defaultdict(lambda: defaultdict(int))
        severity_histo: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        traces: dict[str, list[dict]] = defaultdict(list)
        metrics: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        template_texts: dict[str, str] = {}
        bloom_data: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))

        for service_dir in self._iter_service_dirs():
            service = os.path.basename(service_dir)
            for part_file in sorted(os.listdir(service_dir)):
                if not part_file.endswith('.jsonl'):
                    continue
                part_path = os.path.join(service_dir, part_file)
                minute = part_file[:-6]  # strip .jsonl

                with open(part_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        event = Event.from_json(line)

                        if event.source_type == 'log':
                            if event.template_id:
                                template_ts[service][(event.template_id, minute)] += 1
                                template_texts[event.template_id] = event.template_text
                            severity_histo[service][minute][event.severity] += 1

                            tokens = event.raw.lower().split()
                            for token in tokens:
                                bloom_data[service][minute].add(token)

                        elif event.source_type == 'trace':
                            if event.trace_id:
                                span_info = {
                                    'span_id': event.span_id,
                                    'parent_span_id': event.parent_span_id,
                                    'service': event.service,
                                    'operation': event.template_id,
                                    'duration_ms': event.params.get('duration_ms', 0),
                                    'status': 'ERROR' if event.severity == 'ERROR' else 'OK',
                                    'timestamp_ns': event.timestamp_ns,
                                }
                                traces[event.trace_id].append(span_info)

                        elif event.source_type == 'metric':
                            metric_name = event.template_id
                            metrics[service][metric_name].append({
                                'timestamp_ns': event.timestamp_ns,
                                'value': event.params.get('value', 0),
                            })

                        elif event.source_type == 'k8s_event':
                            severity_histo[service][minute][event.severity] += 1

        self._write_template_ts(template_ts, stats)
        self._write_severity(severity_histo, stats)
        self._write_traces(traces, stats)
        self._write_metrics(metrics, stats)
        self._write_template_texts(template_texts)
        self._write_bloom_filters(bloom_data, stats)

        return stats

    def _iter_service_dirs(self):
        if not os.path.exists(self.event_store_path):
            return
        for entry in sorted(os.listdir(self.event_store_path)):
            path = os.path.join(self.event_store_path, entry)
            if os.path.isdir(path):
                yield path

    def _write_template_ts(self, data, stats):
        out_dir = os.path.join(self.index_path, 'template_ts')
        os.makedirs(out_dir, exist_ok=True)
        for service, counts in data.items():
            rows = []
            for (tid, minute), count in counts.items():
                rows.append({'template_id': tid, 'minute': minute, 'count': count})
            rows.sort(key=lambda r: (r['minute'], r['template_id']))
            path = os.path.join(out_dir, f'{service}.jsonl')
            with open(path, 'w') as f:
                for row in rows:
                    f.write(json.dumps(row, separators=(',', ':')) + '\n')
            stats['templates'] += len(rows)

    def _write_severity(self, data, stats):
        out_dir = os.path.join(self.index_path, 'severity')
        os.makedirs(out_dir, exist_ok=True)
        for service, minutes in data.items():
            rows = []
            for minute, counts in sorted(minutes.items()):
                row = {'minute': minute}
                row.update(counts)
                rows.append(row)
            path = os.path.join(out_dir, f'{service}.jsonl')
            with open(path, 'w') as f:
                for row in rows:
                    f.write(json.dumps(row, separators=(',', ':')) + '\n')
            stats['severity_entries'] += len(rows)

    def _write_traces(self, data, stats):
        path = os.path.join(self.index_path, 'traces.jsonl')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            for trace_id, spans in sorted(data.items()):
                spans.sort(key=lambda s: s['timestamp_ns'])
                row = {'trace_id': trace_id, 'spans': spans}
                f.write(json.dumps(row, separators=(',', ':')) + '\n')
        stats['traces'] = len(data)

    def _write_metrics(self, data, stats):
        base = os.path.join(self.index_path, 'metrics')
        total = 0
        for service, metric_dict in data.items():
            for metric_name, points in metric_dict.items():
                points.sort(key=lambda p: p['timestamp_ns'])
                out_dir = os.path.join(base, service)
                os.makedirs(out_dir, exist_ok=True)
                path = os.path.join(out_dir, f'{metric_name}.jsonl')
                with open(path, 'w') as f:
                    for pt in points:
                        f.write(json.dumps(pt, separators=(',', ':')) + '\n')
                total += 1
        stats['metric_series'] = total

    def _write_template_texts(self, data):
        path = os.path.join(self.index_path, 'template_texts.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, separators=(',', ':'))

    def _write_bloom_filters(self, data, stats):
        base = os.path.join(self.index_path, 'bloom')
        total = 0
        for service, minutes in data.items():
            for minute, tokens in minutes.items():
                out_dir = os.path.join(base, service)
                os.makedirs(out_dir, exist_ok=True)
                path = os.path.join(out_dir, f'{minute}.bloom')
                bf = SimpleBloomFilter(capacity=max(len(tokens), 100))
                for token in tokens:
                    bf.add(token)
                bf.save(path)
                total += 1
        stats['bloom_filters'] = total


class SimpleBloomFilter:
    def __init__(self, capacity: int = 1000, fp_rate: float = 0.01) -> None:
        import math
        self.size = max(64, int(-capacity * math.log(fp_rate) / (math.log(2) ** 2)))
        self.num_hashes = max(1, int((self.size / max(capacity, 1)) * math.log(2)))
        self._bits = bytearray(self.size // 8 + 1)

    def _hash(self, item: str, seed: int) -> int:
        h = hashlib.md5(f'{seed}:{item}'.encode()).digest()
        return int.from_bytes(h[:4], 'little') % self.size

    def add(self, item: str) -> None:
        for i in range(self.num_hashes):
            pos = self._hash(item, i)
            self._bits[pos // 8] |= (1 << (pos % 8))

    def check(self, item: str) -> bool:
        for i in range(self.num_hashes):
            pos = self._hash(item, i)
            if not (self._bits[pos // 8] & (1 << (pos % 8))):
                return False
        return True

    def save(self, path: str) -> None:
        with open(path, 'wb') as f:
            f.write(struct.pack('<II', self.size, self.num_hashes))
            f.write(bytes(self._bits))

    @classmethod
    def load(cls, path: str) -> SimpleBloomFilter:
        with open(path, 'rb') as f:
            size, num_hashes = struct.unpack('<II', f.read(8))
            bits = bytearray(f.read())
        bf = cls.__new__(cls)
        bf.size = size
        bf.num_hashes = num_hashes
        bf._bits = bits
        return bf
