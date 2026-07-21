#!/usr/bin/env python3
"""HERMES FW-08 F2-R1 authority-resolver contract v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (§9).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F2-R1). This is THE external authority boundary. AUTHENTICITY is conferred by a resolver
RETURNING an authority root / manifest — a caller cannot obtain a governed root without a working resolver.

  * `ProductionAuthorityResolver` is the GOVERNED_EXTERNAL_RESOLVER, but in THIS WO it is STRUCTURALLY
    non-functional: every method raises `ResolverNotConfigured`. A REAL candidate therefore CANNOT be
    activated in this WO — the real external integration is outstanding (F2-R2/R3).
  * `SyntheticAuthorityResolver` is the SYNTHETIC_TEST_RESOLVER used by tests only. It returns pre-built
    synthetic artifacts for exact-match queries, else raises `UntrustedAuthorityResolved`.

There is NO hidden filesystem / `/etc` / environment fallback anywhere — a missing resolver fails closed.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

import abc
from typing import List, Optional, Tuple

CONTRACT_VERSION = "1"

RESOLVER_CLASSIFICATIONS = frozenset({"GOVERNED_EXTERNAL_RESOLVER", "SYNTHETIC_TEST_RESOLVER"})


class ResolverNotConfigured(Exception):
    """Raised when no external authority root/manifest is configured — real F2-R1 integration outstanding."""


class UntrustedAuthorityResolved(Exception):
    """Raised when a resolver is asked for an artifact it cannot vouch for (no exact match)."""


class AuthorityResolver(abc.ABC):
    """The external authority boundary. Trust is conferred by RETURNING a root/manifest, never by a caller
    constructing one."""

    @property
    @abc.abstractmethod
    def resolver_classification(self) -> str:  # {"GOVERNED_EXTERNAL_RESOLVER","SYNTHETIC_TEST_RESOLVER"}
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def resolver_identity(self) -> str:
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def resolver_version(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def resolve_authority_root(self, *, authority_root_id: str, trust_domain_id: str, now_utc: str):
        raise NotImplementedError

    @abc.abstractmethod
    def resolve_manifest(self, *, authority_root_id: str, registry_id: str, registry_version: str,
                         registry_digest: str, now_utc: str):
        raise NotImplementedError


class ProductionAuthorityResolver(AuthorityResolver):
    """The GOVERNED external resolver. In this WO it is STRUCTURALLY non-functional — every resolution raises
    `ResolverNotConfigured`. Real mode therefore has NO working resolver here (F2-R1 real integration
    outstanding)."""

    def __init__(self, *, resolver_identity: str = "hermes-fw08-production-authority-resolver",
                 resolver_version: str = "0-unconfigured") -> None:
        self._identity = resolver_identity
        self._version = resolver_version

    @property
    def resolver_classification(self) -> str:
        return "GOVERNED_EXTERNAL_RESOLVER"

    @property
    def resolver_identity(self) -> str:
        return self._identity

    @property
    def resolver_version(self) -> str:
        return self._version

    def resolve_authority_root(self, *, authority_root_id: str, trust_domain_id: str, now_utc: str):
        raise ResolverNotConfigured(
            "no external authority root configured — F2-R1 real integration outstanding")

    def resolve_manifest(self, *, authority_root_id: str, registry_id: str, registry_version: str,
                         registry_digest: str, now_utc: str):
        raise ResolverNotConfigured(
            "no external authority root configured — F2-R1 real integration outstanding")


class SyntheticAuthorityResolver(AuthorityResolver):
    """The SYNTHETIC test resolver. Constructed with a pre-built synthetic root + manifest; returns them for
    exact-matching queries, else raises `UntrustedAuthorityResolved`. NEVER usable in REAL_CANDIDATE mode
    (guarded by `require_resolver`)."""

    def __init__(self, *, authority_root, manifest,
                 resolver_identity: str = "hermes-fw08-synthetic-test-resolver",
                 resolver_version: str = "1") -> None:
        self._root = authority_root
        self._manifest = manifest
        self._identity = resolver_identity
        self._version = resolver_version

    @property
    def resolver_classification(self) -> str:
        return "SYNTHETIC_TEST_RESOLVER"

    @property
    def resolver_identity(self) -> str:
        return self._identity

    @property
    def resolver_version(self) -> str:
        return self._version

    def resolve_authority_root(self, *, authority_root_id: str, trust_domain_id: str, now_utc: str):
        r = self._root
        if r is None or getattr(r, "authority_root_id", None) != authority_root_id \
                or getattr(r, "trust_domain_id", None) != trust_domain_id:
            raise UntrustedAuthorityResolved(
                "synthetic resolver has no exact match for the requested authority root")
        return r

    def resolve_manifest(self, *, authority_root_id: str, registry_id: str, registry_version: str,
                         registry_digest: str, now_utc: str):
        m = self._manifest
        if m is None or getattr(m, "authority_root_id", None) != authority_root_id \
                or getattr(m, "registry_id", None) != registry_id \
                or getattr(m, "registry_policy_version", None) != registry_version \
                or getattr(m, "registry_digest", None) != registry_digest:
            raise UntrustedAuthorityResolved(
                "synthetic resolver has no exact match for the requested manifest")
        return m


def require_resolver(resolver: object, *, real_candidate_mode: bool) -> Tuple[str, ...]:
    """Fail-closed resolver gate. Returns () iff a usable resolver is present for the mode, else sorted
    reasons:
      * None → RS-RESOLVER-MISSING;
      * not an AuthorityResolver instance (e.g. an inline dict/mapping) → RS-RESOLVER-NOT-INTERFACE;
      * real_candidate_mode True and classification == 'SYNTHETIC_TEST_RESOLVER' → RS-SYNTHETIC-IN-REAL-MODE.
    NO hidden filesystem/`/etc`/env fallback anywhere."""
    reasons: List[str] = []
    if resolver is None:
        return ("RS-RESOLVER-MISSING",)
    if not isinstance(resolver, AuthorityResolver):
        return ("RS-RESOLVER-NOT-INTERFACE",)
    if real_candidate_mode and resolver.resolver_classification == "SYNTHETIC_TEST_RESOLVER":
        reasons.append("RS-SYNTHETIC-IN-REAL-MODE")
    return tuple(sorted(set(reasons)))
