#!/usr/bin/env python3
"""HERMES FW-08 evidence-chain manifest + Merkle binding v1 (PURE).

WO-HELM-HERMES-PR114-FW08-FINAL-PREBUILD-TRUST-BOUNDARY-CORRECTIONS-0001 (R-1 §8).
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

WHY THIS EXISTS (R2D2 R-1 §8). Readiness must consume a VALIDATED evidence CHAIN, not a freely-assembled list.
The chain manifest binds ALL gate records under one candidate + source + image, in a DETERMINISTIC canonical
order, with a per-evidence checksum, a producer-trust reference, and a Merkle root over the sealed leaves.
Validation recomputes the root from the supplied sealed records and rejects any missing / reordered /
appended-untrusted / removed / duplicate / conflicting / wrong-candidate / wrong-image / wrong-source / stale
record. Only the VALIDATED chain reaches the trusted-readiness evaluator.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

import design.hermes_fw08_readiness_evidence_v1 as ee
import design.hermes_fw08_producer_trust_v1 as pt

CONTRACT_VERSION = "1"

# The canonical, deterministic gate order the chain MUST present (single source of truth = evidence module).
CANONICAL_ORDER: Tuple[str, ...] = ee.GATE_ORDER


def _leaf_checksum(sealed: "pt.SealedGateEvidence") -> str:
    """Per-evidence leaf: binds the record's evidence_checksum + producer + seal (so a re-sealed or
    re-producered leaf changes the root)."""
    payload = {
        "gate_id": sealed.evidence.gate_id,
        "evidence_checksum": sealed.evidence.evidence_checksum,
        "producer_id": sealed.producer_id,
        "seal": sealed.seal,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def merkle_root(leaves: Sequence[str]) -> str:
    """Deterministic binary Merkle root over hex-string leaves. Empty -> sha256(b""). Odd level duplicates
    the last node (standard). Order-sensitive: any reorder changes the root."""
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level = [bytes.fromhex(x) for x in leaves]
    while len(level) > 1:
        nxt: List[bytes] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            nxt.append(hashlib.sha256(left + right).digest())
        level = nxt
    return level[0].hex()


@dataclass(frozen=True)
class EvidenceChainManifest:
    """§8 manifest binding every gate record under one candidate/source/image with a Merkle root."""

    contract_version: str
    candidate_id: str
    source_sha: str
    image_id: str
    ordered_gate_ids: Tuple[str, ...]
    leaf_checksums: Tuple[str, ...]
    producer_trust_ref: str
    chain_root: str
    lifecycle_state: str
    created_utc: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "candidate_id": self.candidate_id,
            "source_sha": self.source_sha,
            "image_id": self.image_id,
            "ordered_gate_ids": list(self.ordered_gate_ids),
            "leaf_checksums": list(self.leaf_checksums),
            "producer_trust_ref": self.producer_trust_ref,
            "chain_root": self.chain_root,
            "lifecycle_state": self.lifecycle_state,
            "created_utc": self.created_utc,
        }


def _order_records(sealed_records: Sequence["pt.SealedGateEvidence"]) -> List["pt.SealedGateEvidence"]:
    """Return records sorted into the canonical gate order (stable within a gate). Records whose gate is
    unknown are placed last so the count/order check will reject them."""
    def key(s: "pt.SealedGateEvidence") -> Tuple[int, str]:
        return (ee._GATE_INDEX.get(s.evidence.gate_id, len(CANONICAL_ORDER)), s.evidence.gate_id)
    return sorted(sealed_records, key=key)


def build_evidence_chain(
    sealed_records: Sequence["pt.SealedGateEvidence"],
    *,
    candidate_id: str,
    source_sha: str,
    image_id: str,
    producer_trust_ref: str,
    created_utc: str,
    lifecycle_state: str = "ASSEMBLED",
) -> EvidenceChainManifest:
    """Assemble the deterministic chain manifest from the governed wrapper's sealed records."""
    ordered = _order_records(sealed_records)
    gate_ids = tuple(s.evidence.gate_id for s in ordered)
    leaves = tuple(_leaf_checksum(s) for s in ordered)
    return EvidenceChainManifest(
        contract_version=CONTRACT_VERSION, candidate_id=candidate_id, source_sha=source_sha,
        image_id=image_id, ordered_gate_ids=gate_ids, leaf_checksums=leaves,
        producer_trust_ref=producer_trust_ref, chain_root=merkle_root(leaves),
        lifecycle_state=lifecycle_state, created_utc=created_utc,
    )


@dataclass(frozen=True)
class ChainVerdict:
    ready: bool
    reason_codes: Tuple[str, ...]
    chain_root: str

    def to_dict(self) -> Dict[str, object]:
        return {"contract_version": CONTRACT_VERSION, "ready": self.ready,
                "reason_codes": list(self.reason_codes), "chain_root": self.chain_root}


def validate_evidence_chain(
    manifest: "EvidenceChainManifest",
    sealed_records: Sequence["pt.SealedGateEvidence"],
    *,
    verifier: "pt.GovernedEvidenceSealer",
    registry: "pt.ProducerTrustRegistry",
    expected_candidate_id: str,
    expected_source_sha: str,
    expected_image_id: str,
    expected_producer_trust_ref: str,
    now_utc: str,
    max_evidence_age_hours: int = 24,
) -> ChainVerdict:
    """Validate a chain manifest against the supplied sealed records. `ready` is True ONLY if the manifest
    binds the expected candidate/source/image + producer-trust ref, presents EXACTLY the canonical gate set in
    canonical order with no duplicate, the Merkle root recomputed from the supplied records matches the
    manifest root (rejects append / remove / reorder / tamper), AND the trusted-readiness evaluation over the
    sealed records passes (seal authority + typed-evidence structure + freshness)."""
    fail: List[str] = []
    if manifest.contract_version != CONTRACT_VERSION:
        fail.append("CH-CONTRACT-VERSION")
    if manifest.candidate_id != expected_candidate_id:
        fail.append("CH-CANDIDATE-MISMATCH")
    if manifest.source_sha != expected_source_sha:
        fail.append("CH-SOURCE-MISMATCH")
    if manifest.image_id != expected_image_id:
        fail.append("CH-IMAGE-MISMATCH")
    if manifest.producer_trust_ref != expected_producer_trust_ref:
        fail.append("CH-PRODUCER-TRUST-REF-MISMATCH")

    # Canonical gate set + order (rejects missing / removed / appended / reordered / duplicate).
    if manifest.ordered_gate_ids != CANONICAL_ORDER:
        fail.append("CH-ORDER-OR-SET-INVALID")

    # Recompute the root from the SUPPLIED records in canonical order and compare to the manifest.
    ordered = _order_records(sealed_records)
    supplied_gate_ids = tuple(s.evidence.gate_id for s in ordered)
    if supplied_gate_ids != CANONICAL_ORDER:
        fail.append("CH-SUPPLIED-ORDER-OR-SET-INVALID")
    recomputed_leaves = tuple(_leaf_checksum(s) for s in ordered)
    recomputed_root = merkle_root(recomputed_leaves)
    if recomputed_leaves != tuple(manifest.leaf_checksums):
        fail.append("CH-LEAF-CHECKSUM-MISMATCH")
    if recomputed_root != manifest.chain_root:
        fail.append("CH-ROOT-MISMATCH")

    if fail:
        return ChainVerdict(False, tuple(sorted(set(fail))), recomputed_root)

    # Chain shape proven — now prove authority + typed evidence via the trusted-readiness evaluator.
    tv = pt.evaluate_trusted_readiness(
        sealed_records, verifier=verifier, registry=registry,
        expected_source_sha=expected_source_sha, expected_image_id=expected_image_id,
        expected_candidate_id=expected_candidate_id, now_utc=now_utc,
        max_evidence_age_hours=max_evidence_age_hours,
    )
    if not tv.ready:
        return ChainVerdict(False, tuple(sorted(set(tv.reason_codes))), recomputed_root)
    return ChainVerdict(True, tuple(), recomputed_root)
