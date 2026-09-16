from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

# Pre-compiled regexes for format detection
_RE_NGINX = re.compile(r'^([\d.]+) - - \[')
_RE_LOG4J = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) '
    r'(DEBUG|INFO|WARN|ERROR|FATAL) '
    r'\[([^\]]+)\] \[([^\]]+)\] '
    r'(\S+) - (.*)'
)
_RE_REDIS = re.compile(r'^(\d+):([A-Z]) (\d+ \w{3} \d{4} \d{2}:\d{2}:\d{2}\.\d{3}) (.+)')
_RE_POSTGRES = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) UTC '
    r'\[(\d+)\] '
    r'(LOG|ERROR|WARNING|FATAL|PANIC|DEBUG|INFO|NOTICE):  (.*)'
)
_RE_PYTHON = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - '
    r'(\S+) - '
    r'(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL) - (.*)'
)
_RE_NGINX_FIELDS = re.compile(
    r'^([\d.]+) - - \[([^\]]+)\] '
    r'"(\S+) (\S+) HTTP/[^"]*" (\d+) (\d+) '
    r'"([^"]*)" "([^"]*)"(?: rt=([\d.]+))?'
)

MONTHS = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}


@dataclass
class ParsedLine:
    timestamp_raw: str
    timestamp_ns: int
    severity: str
    service: str
    message: str
    format_type: str
    trace_id: str | None = None
    span_id: str | None = None
    structured_fields: dict = field(default_factory=dict)
    raw: str = ""


def _ymdhms_to_epoch(y: int, mo: int, d: int, h: int, mi: int, s: int) -> int:
    import calendar
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def _iso_to_ns(ts: str) -> int:
    y = int(ts[0:4])
    mo = int(ts[5:7])
    d = int(ts[8:10])
    h = int(ts[11:13])
    mi = int(ts[14:16])
    s = int(ts[17:19])
    ms = int(ts[20:23]) if len(ts) > 20 else 0
    epoch = _ymdhms_to_epoch(y, mo, d, h, mi, s)
    return epoch * 1_000_000_000 + ms * 1_000_000


def _space_ts_to_ns(ts: str) -> int:
    y = int(ts[0:4])
    mo = int(ts[5:7])
    d = int(ts[8:10])
    h = int(ts[11:13])
    mi = int(ts[14:16])
    s = int(ts[17:19])
    ms = 0
    if len(ts) > 19 and ts[19] in ('.', ','):
        ms = int(ts[20:23])
    epoch = _ymdhms_to_epoch(y, mo, d, h, mi, s)
    return epoch * 1_000_000_000 + ms * 1_000_000


def _nginx_ts_to_ns(ts: str) -> int:
    # "15/Jan/2024:15:32:01 +0000"
    d = int(ts[0:2])
    mo = MONTHS.get(ts[3:6], 1)
    y = int(ts[7:11])
    h = int(ts[12:14])
    mi = int(ts[15:17])
    s = int(ts[18:20])
    epoch = _ymdhms_to_epoch(y, mo, d, h, mi, s)
    return epoch * 1_000_000_000


def _redis_ts_to_ns(ts: str) -> int:
    # "15 Jan 2024 15:32:01.234"
    parts = ts.split()
    d = int(parts[0])
    mo = MONTHS.get(parts[1], 1)
    y = int(parts[2])
    time_part = parts[3]
    h = int(time_part[0:2])
    mi = int(time_part[3:5])
    s = int(time_part[6:8])
    ms = int(time_part[9:12]) if len(time_part) > 8 else 0
    epoch = _ymdhms_to_epoch(y, mo, d, h, mi, s)
    return epoch * 1_000_000_000 + ms * 1_000_000


def _pg_ts_to_ns(ts: str) -> int:
    # "2024-01-15 15:32:01.234 UTC"
    return _space_ts_to_ns(ts[:23])


def _normalize_severity(s: str) -> str:
    s = s.upper()
    if s == 'WARNING' or s == 'CRITICAL':
        return 'WARN' if s == 'WARNING' else 'FATAL'
    return s


def parse_zerolog(line: str, service: str) -> ParsedLine:
    data = json.loads(line)
    ts_raw = data.get('time', '')
    ts_ns = _iso_to_ns(ts_raw) if ts_raw else 0
    severity = _normalize_severity(data.get('level', 'INFO'))
    msg = data.get('msg', '')
    trace_id = data.get('trace_id')
    span_id = data.get('span_id')
    svc = data.get('service', service)
    fields = {k: v for k, v in data.items()
              if k not in ('level', 'time', 'msg', 'trace_id', 'span_id', 'service')}
    return ParsedLine(
        timestamp_raw=ts_raw, timestamp_ns=ts_ns, severity=severity,
        service=svc, message=msg, format_type='ZEROLOG',
        trace_id=trace_id, span_id=span_id,
        structured_fields=fields, raw=line,
    )


def parse_nginx(line: str, service: str) -> ParsedLine:
    m = _RE_NGINX_FIELDS.match(line)
    if not m:
        return ParsedLine(
            timestamp_raw='', timestamp_ns=0, severity='INFO',
            service=service, message=line, format_type='NGINX_ACCESS', raw=line,
        )
    client_ip, ts_raw, method, url, status, body_size, referer, ua, rt = m.groups()
    ts_ns = _nginx_ts_to_ns(ts_raw)
    status_int = int(status)
    severity = 'ERROR' if status_int >= 500 else ('WARN' if status_int >= 400 else 'INFO')
    msg = f'{method} {url} {status}'
    fields = {
        'client_ip': client_ip, 'method': method, 'url': url,
        'status_code': status_int, 'body_size': int(body_size),
        'user_agent': ua,
    }
    if rt:
        fields['response_time'] = float(rt)
    return ParsedLine(
        timestamp_raw=ts_raw, timestamp_ns=ts_ns, severity=severity,
        service=service, message=msg, format_type='NGINX_ACCESS',
        structured_fields=fields, raw=line,
    )


def parse_log4j(line: str, service: str) -> ParsedLine:
    m = _RE_LOG4J.match(line)
    if not m:
        return ParsedLine(
            timestamp_raw='', timestamp_ns=0, severity='INFO',
            service=service, message=line, format_type='LOG4J', raw=line,
        )
    ts_raw, severity, svc_name, thread, logger, msg = m.groups()
    ts_ns = _space_ts_to_ns(ts_raw)
    severity = _normalize_severity(severity)
    fields = {'thread': thread, 'logger': logger, 'service_tag': svc_name}
    return ParsedLine(
        timestamp_raw=ts_raw, timestamp_ns=ts_ns, severity=severity,
        service=service, message=msg, format_type='LOG4J',
        structured_fields=fields, raw=line,
    )


def parse_redis(line: str, service: str) -> ParsedLine:
    m = _RE_REDIS.match(line)
    if not m:
        return ParsedLine(
            timestamp_raw='', timestamp_ns=0, severity='INFO',
            service=service, message=line, format_type='REDIS_NATIVE', raw=line,
        )
    _pid, role, ts_raw, msg = m.groups()
    ts_ns = _redis_ts_to_ns(ts_raw)
    if msg.startswith('# '):
        severity = 'WARN'
        msg = msg[2:]
    elif msg.startswith('* '):
        severity = 'INFO'
        msg = msg[2:]
    else:
        severity = 'INFO'
    return ParsedLine(
        timestamp_raw=ts_raw, timestamp_ns=ts_ns, severity=severity,
        service=service, message=msg, format_type='REDIS_NATIVE',
        structured_fields={'role': role}, raw=line,
    )


def parse_postgres(line: str, service: str) -> ParsedLine:
    m = _RE_POSTGRES.match(line)
    if not m:
        return ParsedLine(
            timestamp_raw='', timestamp_ns=0, severity='INFO',
            service=service, message=line, format_type='POSTGRES_NATIVE', raw=line,
        )
    ts_raw, pid, level, msg = m.groups()
    ts_ns = _pg_ts_to_ns(ts_raw)
    severity = _normalize_severity(level)
    return ParsedLine(
        timestamp_raw=ts_raw, timestamp_ns=ts_ns, severity=severity,
        service=service, message=msg, format_type='POSTGRES_NATIVE',
        structured_fields={'pid': int(pid)}, raw=line,
    )


def parse_python_stdlib(line: str, service: str) -> ParsedLine:
    m = _RE_PYTHON.match(line)
    if not m:
        return ParsedLine(
            timestamp_raw='', timestamp_ns=0, severity='INFO',
            service=service, message=line, format_type='PYTHON_STDLIB', raw=line,
        )
    ts_raw, logger, severity, msg = m.groups()
    ts_ns = _space_ts_to_ns(ts_raw)
    severity = _normalize_severity(severity)
    return ParsedLine(
        timestamp_raw=ts_raw, timestamp_ns=ts_ns, severity=severity,
        service=service, message=msg, format_type='PYTHON_STDLIB',
        structured_fields={'logger': logger}, raw=line,
    )


def detect_and_parse(line: str, service: str) -> ParsedLine:
    stripped = line.rstrip('\n\r')
    if not stripped:
        return ParsedLine(
            timestamp_raw='', timestamp_ns=0, severity='DEBUG',
            service=service, message='', format_type='UNKNOWN', raw=stripped,
        )

    if stripped[0] == '{':
        try:
            data = json.loads(stripped)
            if 'level' in data and 'time' in data and 'msg' in data:
                return parse_zerolog(stripped, service)
            if 'timestamp' in data and 'severity' in data:
                return ParsedLine(
                    timestamp_raw=str(data.get('timestamp', '')),
                    timestamp_ns=0, severity=data.get('severity', 'INFO'),
                    service=service, message=json.dumps(data),
                    format_type='GENERIC_JSON',
                    structured_fields=data, raw=stripped,
                )
            return ParsedLine(
                timestamp_raw='', timestamp_ns=0, severity='INFO',
                service=service, message=json.dumps(data),
                format_type='UNKNOWN_JSON',
                structured_fields=data, raw=stripped,
            )
        except json.JSONDecodeError:
            pass

    if _RE_NGINX.match(stripped):
        return parse_nginx(stripped, service)

    if _RE_LOG4J.match(stripped):
        return parse_log4j(stripped, service)

    if _RE_REDIS.match(stripped):
        return parse_redis(stripped, service)

    if _RE_POSTGRES.match(stripped):
        return parse_postgres(stripped, service)

    if _RE_PYTHON.match(stripped):
        return parse_python_stdlib(stripped, service)

    return ParsedLine(
        timestamp_raw='', timestamp_ns=0, severity='INFO',
        service=service, message=stripped, format_type='UNKNOWN', raw=stripped,
    )


def service_from_filename(filename: str) -> str:
    name = filename.rsplit('/', 1)[-1]
    if name.endswith('.log'):
        return name[:-4]
    return name
