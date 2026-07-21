#!/usr/bin/env python3
"""HERMES FW-08 F2-R1-C module-owned trust-anchor provider v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (§5,§6,§7,§8, F2-R1-C).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F2-R1-C / AMBER C1..C5). The F2-R1 build still let the CALLER supply the trust:
  * C1 the resolver was caller-supplied and accepted in real mode;
  * C2 the caller supplied the SAME activation_authority to SEAL and VERIFY the handle — owning both sides;
  * C3 approvers were checked only against the root's OWN named approver set, not an EXTERNAL anchor;
  * C4 revocation was captured at handle creation but not RE-checked at candidate-readiness USE;
  * C5 the raw-registry path was not mechanically excluded from REAL_CANDIDATE mode.

THE MODULE-OWNED TRUST BOUNDARY. Real-candidate trust CANNOT be supplied by the caller. This module owns:
  1. a trust-anchor PROVIDER — the PRODUCTION provider is UNAVAILABLE here (fails closed), so REAL_CANDIDATE
     can NEVER activate/validate in this WO; a module-owned SYNTHETIC provider serves TEST_ONLY /
     INERT_SIMULATION and is NOT caller-substitutable (gated by a module-private token);
  2. a module-owned VERIFIER (`_ModuleVerifier`, ephemeral in-process HMAC, mirroring ActivationAuthority)
     that SEALS AND VERIFIES handles for the trusted path — the caller never supplies it (fixes C2);
  3. resolver acceptance keyed on the resolver's exact identity + content-digest listed in the anchor set,
     not isinstance/classification/caller-id/checksum alone (fixes C1);
  4. approver acceptance keyed on membership in the anchor set, not just the root's own list (fixes C3);
  5. revocation re-obtained from the trusted boundary at USE (fixes C4);
  6. a typed candidate mode: REAL_CANDIDATE always fails closed here (fixes C5, via the unavailable provider).

PURE except the ephemeral verifier seal key (`secrets.token_bytes(32)`), mirroring GovernedEvidenceSealer /
ActivationAuthority: no I/O, no subprocess, no network, stdlib only, deterministic given a fixed key. NOT
imported by runtime; does NOT import the active-import module (avoids cycles). The PRODUCTION path performs NO
filesystem / /etc / environment lookup — it fails closed unconditionally.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import inspect
import json
import secrets
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

import design.hermes_fw08_candidate_mode_v1 as cm

CONTRACT_VERSION = "1"

TRUST_ANCHOR_CLASSIFICATIONS = frozenset({
    "SYNTHETIC_TEST_TRUST_ANCHOR",
    "EXTERNALLY_GOVERNED_TRUST_ANCHOR",
    "REVOKED_TRUST_ANCHOR",
    "UNTRUSTED_TRUST_ANCHOR",
})


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ============================================================================ §5 trust anchor set
@dataclass(frozen=True)
class TrustAnchorSet:
    """The §5 externally-governed statement of WHO is trusted. Binds resolver ids + their content digests,
    the module verifier identity (a REFERENCE — the real key is module-owned, never here), the authority
    roots (by id + ref), the approvers (by id), the policy window + provenance, and a self digest. A caller
    can CONSTRUCT one (it is a frozen dataclass), but constructing it does NOT confer trust — only the module
    provider wraps it into a TrustContext carrying the module verifier."""

    contract_version: str
    application: str
    trust_domain_id: str
    trusted_resolver_ids: Tuple[str, ...]
    trusted_resolver_digests: Tuple[str, ...]          # parallel to trusted_resolver_ids
    trusted_activation_verifier_id: str
    trusted_activation_verifier_material: str           # a REFERENCE string; the real key is module-owned
    trusted_authority_root_ids: Tuple[str, ...]
    trusted_authority_root_material: Tuple[str, ...]     # tuple of ref strings — never key material
    trusted_approver_ids: Tuple[str, ...]
    policy_version: str
    valid_from_utc: str
    valid_until_utc: str
    revocation_source_identity: str
    source_provenance: str
    audit_reference: str
    anchor_set_digest: str
    classification: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the digest covers (EXCLUDES anchor_set_digest itself)."""
        return {
            "contract_version": self.contract_version,
            "application": self.application,
            "trust_domain_id": self.trust_domain_id,
            "trusted_resolver_ids": list(self.trusted_resolver_ids),
            "trusted_resolver_digests": list(self.trusted_resolver_digests),
            "trusted_activation_verifier_id": self.trusted_activation_verifier_id,
            "trusted_activation_verifier_material": self.trusted_activation_verifier_material,
            "trusted_authority_root_ids": list(self.trusted_authority_root_ids),
            "trusted_authority_root_material": list(self.trusted_authority_root_material),
            "trusted_approver_ids": list(self.trusted_approver_ids),
            "policy_version": self.policy_version,
            "valid_from_utc": self.valid_from_utc,
            "valid_until_utc": self.valid_until_utc,
            "revocation_source_identity": self.revocation_source_identity,
            "source_provenance": self.source_provenance,
            "audit_reference": self.audit_reference,
            "classification": self.classification,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["anchor_set_digest"] = self.anchor_set_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return TrustAnchorSet.compute_digest(self.identity_fields())

    def resolver_digest_for(self, resolver_id: str) -> Optional[str]:
        """The authorised content-digest for `resolver_id`, or None if the id is not in the set. Parallel
        id/digest tuples: the i-th id authorises the i-th digest."""
        for rid, dig in zip(self.trusted_resolver_ids, self.trusted_resolver_digests):
            if rid == resolver_id:
                return dig
        return None


def new_trust_anchor_set(**kwargs: object) -> TrustAnchorSet:
    """Construct a TrustAnchorSet with its `anchor_set_digest` computed over identity fields. This blesses the
    set's INTEGRITY only; it does NOT confer trust — the module provider must wrap it in a TrustContext."""
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs["anchor_set_digest"] = ""
    s0 = TrustAnchorSet(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(s0, anchor_set_digest=s0.recompute_digest())


# ============================================================================ §8 module-owned verifier
class _ModuleVerifier:
    """MODULE-OWNED in-process seal capability, mirroring ActivationAuthority. Holds an EPHEMERAL,
    name-mangled `self.__key` (`secrets.token_bytes(32)`) — NO getter, NEVER serialised, NEVER in any
    to_dict/log. Only the module builds/holds this; the caller cannot supply it (fixes C2 — the caller no
    longer owns both the sealing and verifying side)."""

    def __init__(self, *, _key: Optional[bytes] = None) -> None:
        self.__key = _key if _key is not None else secrets.token_bytes(32)

    def _seal(self, payload_bytes: bytes) -> str:
        return hmac.new(self.__key, payload_bytes, hashlib.sha256).hexdigest()

    def verify(self, payload_bytes: bytes, seal: object) -> bool:
        if not isinstance(seal, str):
            return False
        expected = self._seal(payload_bytes)
        return hmac.compare_digest(expected, seal)


# The module's OWN verifier + private provenance sentinel. Neither is caller-constructible/substitutable.
_MODULE_VERIFIER = _ModuleVerifier()
_MODULE_TOKEN = object()          # module-private sentinel: only this module holds a reference to it
_CONTEXT_MARKER = object()        # stamped into every genuine module-built TrustContext


# ============================================================================ §6/§7 trust context
class TrustContext:
    """A module-built statement of the trusted boundary for one activation/validation. It is NOT a
    caller-constructible-TRUSTED object: although the class is importable, a genuine context carries the
    module's private `_CONTEXT_MARKER` and the module verifier — a caller-built TrustContext lacks the marker
    (`is_module_built()` is False) and holds no module verifier, so the activation/validation entry points
    reject it. The provider is the ONLY producer of a trusted context."""

    def __init__(
        self,
        *,
        trust_anchor_set: TrustAnchorSet,
        resolver_authorisations: Mapping[str, str],
        approver_ids: Tuple[str, ...],
        revocation_source: object,
        candidate_mode: str,
        _verifier: object,
        _marker: object,
    ) -> None:
        self.trust_anchor_set = trust_anchor_set
        # id -> authorised content-digest.
        self._resolver_authorisations: Dict[str, str] = dict(resolver_authorisations)
        self._approver_ids: Tuple[str, ...] = tuple(approver_ids)
        self._revocation_source = revocation_source
        self.candidate_mode = candidate_mode
        self.__verifier = _verifier
        self.__marker = _marker

    def is_module_built(self) -> bool:
        """True iff this context carries the module's private marker (i.e. the provider built it). A
        caller-built TrustContext returns False."""
        return self.__marker is _CONTEXT_MARKER

    def verifier(self) -> object:
        """The MODULE verifier (only a module-built context carries it). A caller-built context returns None,
        so it cannot verify a real-path handle."""
        if not self.is_module_built():
            return None
        return self.__verifier

    def resolver_authorisation_for(self, resolver_id: str) -> Optional[str]:
        return self._resolver_authorisations.get(resolver_id)

    def approver_ids(self) -> Tuple[str, ...]:
        return self._approver_ids

    def revocation_source(self) -> object:
        return self._revocation_source


def is_module_trust_context(trust_context: object) -> bool:
    """True iff `trust_context` is a genuine module-built TrustContext whose verifier() IS the module verifier.
    A caller-built fake (even a TrustContext subclass or one constructed with a caller verifier) fails."""
    if not isinstance(trust_context, TrustContext):
        return False
    if not trust_context.is_module_built():
        return False
    return trust_context.verifier() is _MODULE_VERIFIER


# ============================================================================ §6 provider availability
def production_provider_available() -> bool:
    """The PRODUCTION trust-anchor provider is NOT configured in this WO. Real-candidate trust therefore has
    NO source here — REAL_CANDIDATE always fails closed. This performs NO filesystem / /etc / environment
    lookup: it is unconditionally False."""
    return False


def resolve_trust_context(
    *,
    candidate_mode: str,
    revocation_source: object = None,
    synthetic_anchor_set: object = None,
    synthetic_resolver_authorisations: Optional[Mapping[str, str]] = None,
    test_context_token: object = None,
) -> Tuple[Optional[TrustContext], Tuple[str, ...]]:
    """Obtain a module-owned TrustContext for `candidate_mode`, or (None, reasons). Fail-closed:
      * require_mode; invalid → (None, ('TC-INVALID-CANDIDATE-MODE',));
      * REAL_CANDIDATE → ALWAYS (None, ('TC-PRODUCTION-PROVIDER-UNAVAILABLE',)) — no fallback, no synthetic
        substitution, no /etc/env/filesystem lookup (the production provider is unavailable in this WO);
      * TEST_ONLY / INERT_SIMULATION → require `test_context_token is _MODULE_TOKEN` (only the module holds
        it) else (None, ('TC-SYNTHETIC-CONTEXT-REQUIRES-MODULE-TOKEN',)); then build a module-owned context
        wrapping the caller's SYNTHETIC anchor set + resolver authorisations + revocation source, but with the
        MODULE verifier (never a caller verifier)."""
    m_reasons = cm.require_mode(candidate_mode)
    if m_reasons:
        return (None, ("TC-INVALID-CANDIDATE-MODE",))

    if cm.is_real(candidate_mode):
        # No production provider in this WO. NO synthetic substitution, NO fallback, NO filesystem/env lookup.
        return (None, ("TC-PRODUCTION-PROVIDER-UNAVAILABLE",))

    # TEST_ONLY / INERT_SIMULATION synthetic path — module-token gated (not caller-mintable).
    if test_context_token is not _MODULE_TOKEN:
        return (None, ("TC-SYNTHETIC-CONTEXT-REQUIRES-MODULE-TOKEN",))
    if not isinstance(synthetic_anchor_set, TrustAnchorSet):
        return (None, ("TC-SYNTHETIC-ANCHOR-SET-INVALID",))
    if synthetic_anchor_set.anchor_set_digest != synthetic_anchor_set.recompute_digest():
        return (None, ("TC-SYNTHETIC-ANCHOR-SET-TAMPERED",))

    authorisations = dict(synthetic_resolver_authorisations or {})
    ctx = TrustContext(
        trust_anchor_set=synthetic_anchor_set,
        resolver_authorisations=authorisations,
        approver_ids=tuple(synthetic_anchor_set.trusted_approver_ids),
        revocation_source=revocation_source,
        candidate_mode=candidate_mode,
        _verifier=_MODULE_VERIFIER,
        _marker=_CONTEXT_MARKER,
    )
    return (ctx, tuple())


def new_synthetic_test_context(
    *,
    application: str,
    trust_domain_id: str,
    candidate_mode: str = "TEST_ONLY",
    trusted_resolver_ids: Tuple[str, ...] = (),
    trusted_resolver_digests: Tuple[str, ...] = (),
    trusted_approver_ids: Tuple[str, ...] = (),
    revocation_source: object = None,
    valid_from_utc: str = "2026-07-20T00:00:00+00:00",
    valid_until_utc: str = "2027-07-21T00:00:00+00:00",
    classification: str = "SYNTHETIC_TEST_TRUST_ANCHOR",
) -> Tuple[Optional[TrustContext], Tuple[str, ...]]:
    """Module helper tests call to get a VALID module-owned TEST_ONLY/INERT_SIMULATION context. It builds the
    synthetic anchor set and internally passes the module token to `resolve_trust_context` — so tests get a
    genuine module context WITHOUT ever seeing the token. A caller CANNOT replicate this without the token
    (proved by a companion test that calls resolve_trust_context with no token → REQUIRES-MODULE-TOKEN).

    REAL_CANDIDATE is refused here too — a synthetic test context is never a real context."""
    if cm.is_real(candidate_mode):
        return (None, ("TC-PRODUCTION-PROVIDER-UNAVAILABLE",))
    anchor_set = new_trust_anchor_set(
        application=application,
        trust_domain_id=trust_domain_id,
        trusted_resolver_ids=tuple(trusted_resolver_ids),
        trusted_resolver_digests=tuple(trusted_resolver_digests),
        trusted_activation_verifier_id="hermes-fw08-module-verifier-v1",
        trusted_activation_verifier_material="MODULE_OWNED_EPHEMERAL_KEY_REFERENCE",
        trusted_authority_root_ids=(),
        trusted_authority_root_material=(),
        trusted_approver_ids=tuple(trusted_approver_ids),
        policy_version="1",
        valid_from_utc=valid_from_utc,
        valid_until_utc=valid_until_utc,
        revocation_source_identity="hermes-fw08-synthetic-revocation-source-v1",
        source_provenance="SYNTHETIC_TEST",
        audit_reference="AUDIT-F2R1C",
        classification=classification,
    )
    authorisations = {rid: dig for rid, dig in zip(trusted_resolver_ids, trusted_resolver_digests)}
    return resolve_trust_context(
        candidate_mode=candidate_mode,
        revocation_source=revocation_source,
        synthetic_anchor_set=anchor_set,
        synthetic_resolver_authorisations=authorisations,
        test_context_token=_MODULE_TOKEN,
    )


# ============================================================================ §7 resolver content-digest
def resolver_content_digest(resolver: object) -> str:
    """Content digest of a resolver's TYPE: sha256 over its source + module + qualname. A caller subclass /
    proxy / copied-with-changed-metadata resolver has a DIFFERENT digest (different module/qualname/source),
    so it cannot match an anchored digest."""
    t = type(resolver)
    try:
        src = inspect.getsource(t)
    except (OSError, TypeError):
        src = "<no-source:" + t.__module__ + "." + t.__qualname__ + ">"
    payload = src + "\x00" + t.__module__ + "\x00" + t.__qualname__
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_resolver_identity(resolver: object, *, trust_context: object) -> Tuple[str, ...]:
    """Fail-closed resolver anchoring (§7). Returns () iff the resolver's exact identity is listed in the
    trust context's authorised map AND its computed content-digest equals the authorised digest for that id.
    Rejects:
      * a non-module or caller-built trust context → RESOLVER-TRUST-CONTEXT-UNTRUSTED;
      * a resolver whose identity is not in the authorised map → RESOLVER-ID-UNKNOWN;
      * a resolver whose content-digest differs from the anchored digest → RESOLVER-DIGEST-MISMATCH.
    A caller subclass/proxy/copied-with-changed-metadata resolver fails (module/qualname/source digest differs,
    or its id isn't authorised) → RESOLVER-NOT-ANCHORED umbrella when neither an id nor a digest matches."""
    if not is_module_trust_context(trust_context):
        return ("RESOLVER-TRUST-CONTEXT-UNTRUSTED",)
    rid = str(getattr(resolver, "resolver_identity", ""))
    authorised_digest = trust_context.resolver_authorisation_for(rid)
    if authorised_digest is None:
        return ("RESOLVER-NOT-ANCHORED", "RESOLVER-ID-UNKNOWN")
    computed = resolver_content_digest(resolver)
    if computed != authorised_digest:
        return ("RESOLVER-DIGEST-MISMATCH", "RESOLVER-NOT-ANCHORED")
    return tuple()


def verify_approver_anchored(approver_id: object, *, trust_context: object) -> Tuple[str, ...]:
    """Fail-closed approver anchoring (§7/§11). Returns () iff `approver_id` is in the trust context's
    authorised approver ids (the EXTERNAL anchor set, not merely the root's own list — fixes C3). Rejects a
    non-module context → APPROVER-TRUST-CONTEXT-UNTRUSTED; an unanchored approver → APPROVER-NOT-ANCHORED."""
    if not is_module_trust_context(trust_context):
        return ("APPROVER-TRUST-CONTEXT-UNTRUSTED",)
    if str(approver_id) not in set(trust_context.approver_ids()):
        return ("APPROVER-NOT-ANCHORED",)
    return tuple()


def obtain_current_revocation(trust_context: object, *, now_utc: str) -> Tuple[Optional[object], Tuple[str, ...]]:
    """Re-obtain the CURRENT revocation set from the trusted boundary at USE (§12, fixes C4). Returns
    (revocation_set, ()) iff the context carries a revocation source whose integrity_ok() holds now; else
    (None, ('REVOCATION-SOURCE-UNAVAILABLE',)) — fail closed. `now_utc` is accepted for signature parity /
    future time-scoped sources; integrity is time-independent here."""
    if not is_module_trust_context(trust_context):
        return (None, ("REVOCATION-TRUST-CONTEXT-UNTRUSTED",))
    src = trust_context.revocation_source()
    integ = getattr(src, "integrity_ok", None)
    if src is None or not callable(integ):
        return (None, ("REVOCATION-SOURCE-UNAVAILABLE",))
    if not integ():
        return (None, ("REVOCATION-SOURCE-UNAVAILABLE",))
    return (src, tuple())
