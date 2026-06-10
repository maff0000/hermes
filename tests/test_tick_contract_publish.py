"""WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001 — unit/static tests (no live DB/Redis)."""
import json, os, re
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys; sys.path.insert(0, BASE)

from models.tick_contract import HermesTickContract, CONTRACT_VERSION_V1
from utils.tick_seq import TickSeqGenerator
from utils.tick_contract_writer import TickContractWriter
from utils.tick_stream_publisher import TickStreamPublisher

def _read(rel):
    with open(os.path.join(BASE, rel), encoding="utf-8") as f: return f.read()

# ---- migration shape ----
def test_migration_015_adds_seq_and_version():
    s = _read("migrations/015_ticks_seq_contract_version.sql")
    assert "ADD COLUMN seq BIGINT" in s and "ADD COLUMN contract_version" in s
    assert "idx_ticks_instrument_seq" in s
    assert "DROP" not in s.upper()  # additive only

def test_migration_016_config_rows_json_valid():
    s = _read("migrations/016_tick_contract_config.sql")
    keys = ["tick_contract_version","tick_stream_enabled","tick_stream_maxlen","tick_latest_ttl_seconds",
            "tick_retention_warm_days","tick_archive_enabled","tick_archive_path","tick_seq_backfill_batch_size"]
    for k in keys: assert k in s
    blobs = re.findall(r"'(\{.*?\})'\)", s, re.S)
    assert len(blobs) == len(keys)
    for b in blobs:
        d = json.loads(b); assert d.get("rationale") and d.get("source")
    assert "ON DUPLICATE KEY" not in s.upper()

# ---- contract serialisation ----
def test_contract_serialisation():
    c = HermesTickContract("XAU_USD","OANDA",
        datetime(2026,6,10,6,0,tzinfo=timezone.utc), datetime(2026,6,10,6,0,1,tzinfo=timezone.utc),
        Decimal("2300.12345"), Decimal("2300.22345"), 42, CONTRACT_VERSION_V1)
    d = c.to_dict()
    assert set(d) == {"instrument","source","source_ts_utc","received_at_utc","bid","ask","seq","contract_version"}
    assert d["bid"] == "2300.12345" and d["seq"] == 42 and d["source_ts_utc"].endswith("+00:00")
    rf = c.to_redis_fields(datetime(2026,6,10,6,0,2,tzinfo=timezone.utc))
    assert rf["owner"] == "hermes" and rf["published_at_utc"].endswith("+00:00")
    assert rf["contract_version"] == "hermes.tick.v1"
    assert all(isinstance(v,str) for v in rf.values())

# ---- seq generation ----
def test_seq_monotonic_per_instrument():
    g = TickSeqGenerator(seed_fn=lambda i: {"XAU_USD":100,"EUR_USD":0}.get(i,0))
    assert [g.next("XAU_USD") for _ in range(3)] == [101,102,103]
    assert [g.next("EUR_USD") for _ in range(2)] == [1,2]
    assert g.next("XAU_USD") == 104  # independent, monotonic

# ---- writer: fail-loud insert (R1 fix per R2D2/Architect) ----
class _Log:
    def __init__(self): self.msgs=[]
    def info(self,*a): self.msgs.append(a)

class _ICur:   # insert cursor; optionally raises
    def __init__(self, raise_exc=None): self._raise=raise_exc; self.rowcount=0
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def execute(self, sql, params=None):
        if self._raise is not None: raise self._raise
        self.rowcount=1

class _SCur:   # idempotency-confirm cursor
    def __init__(self, exists): self._exists=exists
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def execute(self, sql, params=None): pass
    def fetchone(self): return (1,) if self._exists else None

class _Conn:
    def __init__(self, cursors): self._c=list(cursors); self.committed=False; self.rolled=False
    def __enter__(self): return self
    def __exit__(self,*a): return False
    def cursor(self): return self._c.pop(0)
    def commit(self): self.committed=True
    def rollback(self): self.rolled=True

def _mk_writer(cursors, log=None):
    return TickContractWriter(lambda: _Conn(cursors), TickSeqGenerator(lambda i:0), log or _Log(), "hermes.tick.v1")

def _contract():
    return HermesTickContract("XAU_USD","OANDA",
        datetime(2026,6,10,tzinfo=timezone.utc), datetime(2026,6,10,tzinfo=timezone.utc),
        Decimal("1"), Decimal("2"), 1, "hermes.tick.v1")

def _raises_runtime(fn, substr):
    try:
        fn(); assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert substr in str(e), f"got: {e}"

def test_writer_insert_success_returns_true():
    assert _mk_writer([_ICur(None)]).write(_contract()) is True

def test_writer_genuine_idempotency_duplicate_visible_skip():
    log=_Log()
    dup=Exception(1062, "Duplicate entry for key 'uq_ticks_idempotency'")
    w=_mk_writer([_ICur(dup), _SCur(exists=True)], log=log)
    assert w.write(_contract()) is False                       # skip, not success
    assert any("IDEMPOTENT_SKIP" in str(m) for m in log.msgs)  # visible, not silent

def test_writer_seq_collision_dupkey_not_idempotency_fails_loud():
    dup=Exception(1062, "Duplicate entry for key 'uq_ticks_instrument_seq'")
    w=_mk_writer([_ICur(dup), _SCur(exists=False)])            # dup-key but idempotency row absent
    _raises_runtime(lambda: w.write(_contract()), "GOV-TICK-INSERT-001")

def test_writer_non_duplicate_error_fails_loud():
    trunc=Exception(1265, "Data truncated for column 'bid'")
    w=_mk_writer([_ICur(trunc)])                               # non-1062 -> fail loud, no confirm
    _raises_runtime(lambda: w.write(_contract()), "GOV-TICK-INSERT-001")

def test_writer_zero_rowcount_without_error_fails_loud():
    class _ZeroCur(_ICur):
        def execute(self, sql, params=None): self.rowcount=0  # no raise, 0 rows -> no silent drop
    _raises_runtime(lambda: _mk_writer([_ZeroCur(None)]).write(_contract()), "GOV-TICK-INSERT-001")

def test_writer_requires_contract_version():
    try:
        TickContractWriter(lambda:_Conn([]), TickSeqGenerator(lambda i:0), _Log(), "")
        assert False, "expected fail-loud"
    except ValueError as e:
        assert "contract_version" in str(e)

# ---- redis stream key names + ttl/freshness + gating ----
class _Redis:
    def __init__(self): self.xadds=[]; self.sets=[]
    def xadd(self,k,f,maxlen=None,approximate=None): self.xadds.append((k,f,maxlen,approximate))
    def set(self,k,v,ex=None): self.sets.append((k,v,ex))

def _cfg(d):
    def get(key,vt):
        v=d[key]
        return {"int":int,"bool":lambda x:str(x).lower() in("1","true","yes"),"string":str}[vt](v)
    return get

def test_stream_disabled_is_noop():
    r=_Redis()
    p=TickStreamPublisher(r,_cfg({"tick_stream_enabled":"false"}),lambda:datetime(2026,6,10,tzinfo=timezone.utc))
    c=HermesTickContract("XAU_USD","OANDA",datetime(2026,6,10,tzinfo=timezone.utc),datetime(2026,6,10,tzinfo=timezone.utc),Decimal("1"),Decimal("2"),1,"hermes.tick.v1")
    assert p.publish(c) is False and r.xadds==[] and r.sets==[]

def test_stream_enabled_keys_ttl_freshness():
    r=_Redis()
    p=TickStreamPublisher(r,_cfg({"tick_stream_enabled":"true","tick_stream_maxlen":"100000","tick_latest_ttl_seconds":"120"}),
                          lambda:datetime(2026,6,10,6,0,2,tzinfo=timezone.utc))
    c=HermesTickContract("XAU_USD","OANDA",datetime(2026,6,10,tzinfo=timezone.utc),datetime(2026,6,10,tzinfo=timezone.utc),Decimal("1"),Decimal("2"),7,"hermes.tick.v1")
    assert p.publish(c) is True
    assert p.stream_key("XAU_USD")=="hermes:ticks:XAU_USD"
    assert p.latest_key("XAU_USD")=="hermes:ticks:latest:XAU_USD"
    k,f,maxlen,approx=r.xadds[0]; assert k=="hermes:ticks:XAU_USD" and maxlen==100000 and approx is True
    assert f["published_at_utc"].endswith("+00:00") and f["owner"]=="hermes"
    lk,lv,ex=r.sets[0]; assert lk=="hermes:ticks:latest:XAU_USD" and ex==120

# ---- boundary: no structure_engine import, no Falcon-shaped fields ----
def test_no_structure_engine_or_falcon_imports_or_fields():
    # Boundary is about COUPLING (imports) + Falcon-shaped FIELDS, not documentation prose.
    for rel in ["models/tick_contract.py","utils/tick_seq.py","utils/tick_contract_writer.py",
                "utils/tick_stream_publisher.py","scripts/backfill_tick_seq.py","scripts/tick_retention.py"]:
        for line in _read(rel).splitlines():
            ls=line.strip()
            if ls.startswith(("import ","from ")):
                assert "structure_engine" not in ls, f"{rel}: structure_engine import"
                assert "falcon" not in ls.lower(), f"{rel}: falcon import"
    from dataclasses import fields as _f
    names={f.name for f in _f(HermesTickContract)}
    assert names == {"instrument","source","source_ts_utc","received_at_utc","bid","ask","seq","contract_version"}
