#!/usr/bin/env python3
"""GOLD-AUD-A protocol allow-list checker (PID section S1/S2/S3/S8, acceptance criterion 2).

Pure offline tool. It reads a JSON-lines command log produced by the HELM-operated wrapper and asserts that every
logged command is in the S1 allow-list, none is in S2, and the S3 rate/size limits and pre-check rules held. It
never connects to anything. The same `check_command` function is meant to be imported by the wrapper so the gate
and the audit use one definition.

Log line (one JSON object per command):
  {"conn": "c1", "utc": "2026-10-16T09:00:00.123Z", "argv": ["SCAN","0","MATCH","*XAU_USD*","COUNT","200"],
   "reply_kind": "array", "reply_size": 1234, "reply_value": null}
`reply_value` is filled only for small scalar replies the rules need: TYPE (string), STRLEN/HLEN/ZCARD/SCARD (int).
`argv` never contains a credential; the wrapper authenticates out of band.

usage: python3 -I protocol_allowlist.py <log.jsonl> [--legacy-prefix PFX ...] [--allow-unprefixed-scan]
                                        [--extra-scan PATTERN ...]
exit status 0 only when there are no violations.

Scan patterns are an EXACT allow-list (no globbing logic is trusted): only the prefix-qualified patterns below are
accepted by default; the unprefixed forms need --allow-unprefixed-scan (an extension requiring HELM and PO approval),
and any other pattern needs an explicit --extra-scan naming it. Content reads (GET, HGET, HGETALL, ZRANGE, SMEMBERS,
LRANGE) are allowed only on keys that fully match a governed family pattern, or a legacy family under a
HELM-confirmed prefix given with --legacy-prefix. Metadata commands (TYPE, TTL, PTTL, EXISTS, STRLEN, HLEN, ZCARD,
SCARD) are allowed on any key with the governed prefix that carries the instrument segment (to detect alias or
unexpected keys) and never on keys of other systems.
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone

MAX_SCAN_COUNT = 200
MAX_GET_STRLEN = 64 * 1024
MAX_REPLY_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_CMDS_PER_SEC = 10
MIN_SCAN_GAP_SEC = 1.0
MAX_HGETALL_HLEN = 200
MAX_ZRANGE_ZCARD = 200
MAX_ZRANGE_SPAN = 100
MAX_SMEMBERS_SCARD = 200
MAX_LRANGE_SPAN = 100
INFO_SECTIONS = {"server", "clients", "persistence"}

ALLOWED = {"SCAN", "TYPE", "TTL", "PTTL", "EXISTS", "STRLEN", "GET", "HLEN", "HGET", "HGETALL", "ZCARD", "ZRANGE",
           "SCARD", "SMEMBERS", "LRANGE", "INFO", "DBSIZE", "PING"}
FORBIDDEN = {"KEYS", "SET", "DEL", "UNLINK", "EXPIRE", "PEXPIRE", "PERSIST", "RENAME", "FLUSHDB", "FLUSHALL",
             "CONFIG", "ACL", "SUBSCRIBE", "PSUBSCRIBE", "XREAD", "XREADGROUP", "XACK", "XDEL", "BLPOP", "BRPOP",
             "BLMOVE", "BLMPOP", "BZPOPMIN", "BZPOPMAX", "BRPOPLPUSH", "MONITOR", "DEBUG", "SLOWLOG", "CLIENT",
             "SAVE", "BGSAVE", "SHUTDOWN", "SCRIPT", "EVAL", "EVALSHA", "FUNCTION", "MIGRATE", "REPLICAOF",
             "SLAVEOF", "PUBLISH", "OBJECT", "MEMORY", "AUTH", "SELECT", "MULTI", "EXEC", "WATCH", "XADD", "HSET",
             "ZADD", "LPUSH", "RPUSH", "SADD", "INCR", "SETEX", "GETSET", "GETDEL", "APPEND", "MSET"}
METADATA_CMDS = {"TYPE", "TTL", "PTTL", "STRLEN", "HLEN", "ZCARD", "SCARD", "EXISTS"}
CONTENT_CMDS = {"GET", "HGET", "HGETALL", "ZRANGE", "SMEMBERS", "LRANGE"}
KEY_FIRST = METADATA_CMDS | CONTENT_CMDS

MAX_SCAN_PASSES = 3
MAX_WINDOW_SECONDS = 6 * 3600

APPROVED_SCAN = {"hermes:*XAU_USD*", "hermes:*XAUUSD*"}
UNPREFIXED_SCAN = {"*XAU_USD*", "*XAUUSD*"}     # PROPOSED EXTENSION, needs HELM and PO approval

_TF = r"(?:M1|M5|M15|H1|H4|D1)"
_TF_HIST = r"(?:M1|M5|M15|H1|H4)"
GOVERNED_CONTENT = [re.compile(p) for p in (
    r"^hermes:ticks:XAU_USD:latest:v1$",
    r"^hermes:candles:XAU_USD:" + _TF + r":latest:v1$",
    r"^hermes:candles:XAU_USD:" + _TF + r":history:v1:(?:index|[0-9]+)$",
    r"^hermes:indicators:XAU_USD:" + _TF + r":v1$",
    r"^hermes:indicators:XAU_USD:" + _TF_HIST + r":history:v1:(?:index|[0-9]+)$",
    r"^hermes:candle_features:XAU_USD:" + _TF + r":v1$",
    r"^hermes:sessions:XAU_USD:v1$",
    r"^hermes:levels:XAU_USD:(?:session|intraday|daily|weekly):v1$",
    r"^hermes:quote:XAU_USD:v1$",
    r"^hermes:feed_health:XAU_USD:v1$",
    r"^hermes:instrument_catalog:XAU_USD:v1$",
    r"^hermes:gaps:XAU_USD:v1$",
    r"^hermes:backfill:status:XAU_USD:v1$",
    r"^hermes:ticks:latest:v1$", r"^hermes:publisher:heartbeat:v1$",
    r"^hermes:contract:manifest:v1$", r"^hermes:catalog:candles:v1$", r"^hermes:health:v1$",
)]
LEGACY_FAMILIES = (r"signals:latest:XAU_USD", r"signals:price:XAU_USD", r"market_map:XAU_USD",
                   r"instrument:XAU_USD", r"signals:candle:" + _TF + r":XAU_USD:latest")
METADATA_EXTRA = re.compile(r"hermes:\S*(?:XAU_USD|XAUUSD)\S*")   # alias/unexpected keys under the governed prefix


def _legacy_rx(prefixes):
    return [re.compile("^" + re.escape(p) + f + "$") for p in prefixes for f in LEGACY_FAMILIES]


def _key_allowed(cmd, key, legacy_prefixes):
    if any(rx.fullmatch(key) for rx in GOVERNED_CONTENT) or any(rx.fullmatch(key) for rx in _legacy_rx(legacy_prefixes)):
        return True
    return cmd in METADATA_CMDS and bool(METADATA_EXTRA.fullmatch(key))


def _parse_utc(text):
    return datetime.strptime(text.rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check_command(argv, state, legacy_prefixes=(), allow_unprefixed_scan=False, extra_scan=()):
    """Return a list of violation strings for one command (empty list = allowed).

    `state` carries pre-check results between calls (type, strlen, hlen, zcard, scard per key); the caller updates it
    from replies. `legacy_prefixes` are HELM-confirmed legacy key prefixes; empty by default.
    """
    if not argv or not isinstance(argv, list):
        return ["empty or malformed argv"]
    for a in argv:
        if not isinstance(a, (str, int)) or any(ord(c) <= 32 or ord(c) == 127 for c in str(a)):
            return ["argument contains whitespace, a control character or a non-string value: %r" % (a,)]
    cmd = str(argv[0]).upper()
    args = argv[1:]
    out = []
    if cmd in FORBIDDEN:
        return ["S2 forbidden command %s" % cmd]
    if cmd not in ALLOWED:
        return ["command %s is not in the S1 allow-list" % cmd]

    if cmd in KEY_FIRST:
        if not args:
            return ["%s without a key" % cmd]
        if cmd == "EXISTS" and len(args) != 1:
            out.append("EXISTS must take exactly one key")
        key = str(args[0])
        if not _key_allowed(cmd, key, legacy_prefixes):
            out.append("key not permitted for %s (not a governed family pattern or confirmed legacy prefix): %r" % (cmd, key))
    else:
        key = None

    if cmd == "SCAN":
        # SCAN cursor MATCH pattern COUNT n   (exact shape; no TYPE option, no bare SCAN)
        if len(args) != 5 or str(args[1]).upper() != "MATCH" or str(args[3]).upper() != "COUNT":
            out.append("SCAN must be exactly: SCAN <cursor> MATCH <pattern> COUNT <n>")
        else:
            if _int(args[0]) is None:
                out.append("SCAN cursor is not an integer")
            approved = set(APPROVED_SCAN) | set(extra_scan) | (UNPREFIXED_SCAN if allow_unprefixed_scan else set())
            if str(args[2]) not in approved:
                out.append("SCAN MATCH pattern %r is not in the approved pattern list" % str(args[2]))
            n = _int(args[4])
            if n is None or n < 1 or n > MAX_SCAN_COUNT:
                out.append("SCAN COUNT must be 1..%d" % MAX_SCAN_COUNT)
    elif cmd in ("TYPE", "TTL", "PTTL", "STRLEN", "HLEN", "ZCARD", "SCARD", "HGETALL", "SMEMBERS", "GET"):
        if len(args) != 1:
            out.append("%s takes exactly one argument" % cmd)
        elif cmd == "GET":
            if state.get("type", {}).get(key) != "string":
                out.append("GET without a preceding TYPE == string for %r" % key)
            sl = state.get("strlen", {}).get(key)
            if sl is None or sl > MAX_GET_STRLEN:
                out.append("GET without a preceding STRLEN <= %d for %r" % (MAX_GET_STRLEN, key))
        elif cmd == "HGETALL":
            hl = state.get("hlen", {}).get(key)
            if hl is None or hl > MAX_HGETALL_HLEN:
                out.append("HGETALL without a preceding HLEN <= %d for %r" % (MAX_HGETALL_HLEN, key))
        elif cmd == "SMEMBERS":
            sc = state.get("scard", {}).get(key)
            if sc is None or sc > MAX_SMEMBERS_SCARD:
                out.append("SMEMBERS without a preceding SCARD <= %d for %r" % (MAX_SMEMBERS_SCARD, key))
    elif cmd == "HGET":
        if len(args) != 2:
            out.append("HGET takes a key and one field")
    elif cmd == "ZRANGE":
        if len(args) != 3:
            out.append("ZRANGE takes exactly key start stop (no options)")
        else:
            a, b = _int(args[1]), _int(args[2])
            zc = state.get("zcard", {}).get(key)
            if a is None or b is None or a < 0 or b < a or (b - a + 1) > MAX_ZRANGE_SPAN:
                out.append("ZRANGE bounds must be non-negative and span at most %d elements" % MAX_ZRANGE_SPAN)
            if zc is None or zc > MAX_ZRANGE_ZCARD:
                out.append("ZRANGE without a preceding ZCARD <= %d for %r" % (MAX_ZRANGE_ZCARD, key))
    elif cmd == "LRANGE":
        if len(args) != 3:
            out.append("LRANGE takes exactly key start stop")
        else:
            a, b = _int(args[1]), _int(args[2])
            if a is None or b is None or a < 0 or b < a or (b - a + 1) > MAX_LRANGE_SPAN:
                out.append("LRANGE bounds must be non-negative and span at most %d elements" % MAX_LRANGE_SPAN)
    elif cmd == "INFO":
        if len(args) != 1 or str(args[0]).lower() not in INFO_SECTIONS:
            out.append("INFO requires exactly one section in %s" % sorted(INFO_SECTIONS))
    elif cmd in ("DBSIZE", "PING"):
        if args:
            out.append("%s takes no arguments" % cmd)
    return out


def _record_reply(argv, entry, state):
    cmd = str(argv[0]).upper()
    if len(argv) < 2:
        return
    key = str(argv[1])
    val = entry.get("reply_value")
    if cmd == "TYPE" and isinstance(val, str):
        state.setdefault("type", {})[key] = val
    elif cmd in ("STRLEN", "HLEN", "ZCARD", "SCARD") and isinstance(val, int):
        state.setdefault(cmd.lower(), {})[key] = val


def check_log(lines, legacy_prefixes=(), allow_unprefixed_scan=False, extra_scan=()):
    """Check a whole log in linear time. Returns a report dict with per-command counts and a list of violations."""
    from collections import deque
    state, counts, violations = {}, Counter(), []
    window, last_t, first_t, last_scan, total_bytes, conns, passes = deque(), None, None, None, 0, set(), 0
    for n, raw in enumerate(lines, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
            argv = entry["argv"]
            t = _parse_utc(entry["utc"])
        except (ValueError, KeyError, TypeError):
            violations.append({"line": n, "violation": "unparseable log line"})
            continue
        conns.add(entry.get("conn"))
        cmd = str(argv[0]).upper() if isinstance(argv, list) and argv else "?"
        counts[cmd] += 1
        for v in check_command(argv, state, legacy_prefixes, allow_unprefixed_scan, extra_scan):
            violations.append({"line": n, "cmd": cmd, "violation": v})
        try:
            size = int(entry.get("reply_size") or 0)
            if size < 0:
                raise ValueError
        except (TypeError, ValueError):
            violations.append({"line": n, "cmd": cmd, "violation": "reply_size is not a non-negative integer"})
            size = 0
        total_bytes += size
        if size > MAX_REPLY_BYTES:
            violations.append({"line": n, "cmd": cmd, "violation": "single reply %d bytes > %d" % (size, MAX_REPLY_BYTES)})
        if last_t is not None and t < last_t:
            violations.append({"line": n, "cmd": cmd, "violation": "timestamp earlier than the previous command (log out of order)"})
            t = last_t
        last_t = t
        first_t = first_t or t
        window.append(t)
        while (t - window[0]).total_seconds() >= 1.0:
            window.popleft()
        if len(window) > MAX_CMDS_PER_SEC:
            violations.append({"line": n, "cmd": cmd, "violation": "more than %d commands in one second" % MAX_CMDS_PER_SEC})
        if (t - first_t).total_seconds() > MAX_WINDOW_SECONDS:
            violations.append({"line": n, "cmd": cmd, "violation": "time-box exceeded (%d h)" % (MAX_WINDOW_SECONDS // 3600)})
        if cmd == "SCAN":
            if last_scan is not None and (t - last_scan).total_seconds() < MIN_SCAN_GAP_SEC:
                violations.append({"line": n, "cmd": cmd, "violation": "SCAN pages closer than %.1f s" % MIN_SCAN_GAP_SEC})
            last_scan = t
            if len(argv) > 1 and str(argv[1]) == "0":
                passes += 1
                if passes > MAX_SCAN_PASSES:
                    violations.append({"line": n, "cmd": cmd, "violation": "more than %d scan passes" % MAX_SCAN_PASSES})
        if isinstance(argv, list) and argv:
            _record_reply(argv, entry, state)
    if total_bytes > MAX_TOTAL_BYTES:
        violations.append({"line": 0, "violation": "total payload %d bytes > %d" % (total_bytes, MAX_TOTAL_BYTES)})
    if len(conns) > 1:
        violations.append({"line": 0, "violation": "more than one connection id in the log: %s" % sorted(map(str, conns))})
    return {"commands": sum(counts.values()), "per_command": dict(counts), "scan_passes": passes,
            "total_reply_bytes": total_bytes, "violations": violations, "pass": not violations}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    argv = sys.argv[2:]
    legacy, extra, i = [], [], 0
    unpref = "--allow-unprefixed-scan" in argv
    while i < len(argv):
        if argv[i] == "--legacy-prefix" and i + 1 < len(argv):
            legacy.append(argv[i + 1]); i += 2
        elif argv[i] == "--extra-scan" and i + 1 < len(argv):
            extra.append(argv[i + 1]); i += 2
        else:
            i += 1
    with open(sys.argv[1], encoding="utf-8") as fh:
        report = check_log(fh, tuple(legacy), unpref, tuple(extra))
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["pass"] else 1)
