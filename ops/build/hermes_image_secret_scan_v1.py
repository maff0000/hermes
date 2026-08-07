#!/usr/bin/env python3
"""HERMES image secret-artefact scanner v1.
WO-HELM-HERMES-BUILD-CONTEXT-SECRET-LEAK-CONTAINMENT-AND-CLEAN-SOURCE-BUILD-HARDENING-0001.

Reusable, bounded, fail-closed scan of a BUILT image's filesystem for prohibited secret ARTEFACTS (by PATH/name, not
content dump). Defence-in-depth on top of the exact-SHA clean build context: even if a future change re-introduced a
tracked secret file, a candidate carrying one is REJECTED_SECRET_CONTAMINATED and cannot be promoted.

Mechanism: `docker create` + `docker export` streams the image filesystem as a tar; we inspect ONLY member NAMES
(never contents). No `docker run` (image is not executed). Bounded: single streaming pass, capped member count.

Findings report path + class + verdict ONLY — never a secret value. The known incident sentinel
`app/healthcheck/canary/.env` is an automatic hard rejection (§21).
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
import tarfile

CONTRACT_VERSION = "1"
_DOCKER_TIMEOUT = 120
_MAX_MEMBERS = 500_000
# Only the APPLICATION tree is scanned. The base OS / interpreter trees legitimately ship public CA bundles
# (/etc/ssl/certs/*.pem, site-packages/certifi/cacert.pem), stdlib, etc. — those are NOT application secrets and must
# not be flagged. The leak vector is application content copied into the image, which lives under /app.
_APP_ROOTS = ("app/", "./app/")

# Prohibited artefact classes (by path/name). Each: (class, compiled regex on the in-image posix path).
_RULES = (
    # the known incident sentinel is matched FIRST so it is reported specifically (still a hard reject either way).
    ("KNOWN_CANARY_ENV", re.compile(r"(^|/)healthcheck/canary/\.env$")),
    ("NESTED_ENV", re.compile(r"(^|/)\.env$")),
    ("NESTED_ENV_SUFFIXED", re.compile(r"(^|/)\.env\.[A-Za-z0-9]+$")),
    ("PRIVATE_KEY", re.compile(r"\.(pem|key|p12|pfx)$")),
    ("SSH_KEY", re.compile(r"(^|/)(id_rsa|id_ed25519|id_dsa|id_ecdsa)(\.pub)?$")),
    ("SSH_DIR", re.compile(r"(^|/)\.ssh(/|$)")),
    ("NETRC", re.compile(r"(^|/)\.netrc$")),
    ("AWS_CREDS", re.compile(r"(^|/)\.aws/credentials$")),
    ("CLAUDE", re.compile(r"(^|/)\.claude(/|$)")),
)
# Intentional, non-secret allowlist (templates/examples never carry real values).
_ALLOW = re.compile(r"\.(env\.example|env\.template)$|(^|/)\.env\.example$")

# Rules that are ALWAYS a hard rejection when present.
_HARD_REJECT = {"NESTED_ENV", "NESTED_ENV_SUFFIXED", "KNOWN_CANARY_ENV", "PRIVATE_KEY", "SSH_KEY", "SSH_DIR",
                "NETRC", "AWS_CREDS"}


class ImageScanError(Exception):
    pass


def classify(path: str):
    """Return a rule class for a prohibited artefact path, or None. Allowlisted templates/examples return None."""
    if _ALLOW.search(path):
        return None
    for cls, rx in _RULES:
        if rx.search(path):
            return cls
    return None


def _docker(args, *, capture=True, timeout=_DOCKER_TIMEOUT):
    return subprocess.run(["docker", *args], capture_output=capture, timeout=timeout, check=False)


def _in_app(raw):
    return any(raw.startswith(r) for r in _APP_ROOTS)


def scan_member_names(names):
    """Pure: classify APPLICATION (`app/`) in-image posix paths. Non-app system paths (OS CA bundles, stdlib) are
    out of scope. Returns (ok, findings)."""
    findings = []
    for raw in names:
        if not _in_app(raw):
            continue
        p = raw[len("./"):] if raw.startswith("./") else raw
        cls = classify(p)
        if cls:
            findings.append({"path": p, "class": cls, "verdict": "REJECT" if cls in _HARD_REJECT else "REVIEW"})
    ok = not any(f["verdict"] == "REJECT" for f in findings)
    return ok, findings


def scan_image(image: str):
    """Scan a built image by exporting its filesystem (no execution). Returns dict result. Fail-closed on error."""
    cid = None
    try:
        c = _docker(["create", "--entrypoint", "true", image])
        if c.returncode != 0:
            raise ImageScanError(f"docker create failed: {c.stderr.decode('utf-8', 'replace')[:200]}")
        cid = c.stdout.decode().strip()
        proc = subprocess.Popen(["docker", "export", cid], stdout=subprocess.PIPE)
        names = []
        with tarfile.open(fileobj=proc.stdout, mode="r|*") as tar:
            for i, m in enumerate(tar):
                if i > _MAX_MEMBERS:
                    raise ImageScanError("image member count exceeds bound")
                names.append(m.name)
        proc.wait(timeout=_DOCKER_TIMEOUT)
        ok, findings = scan_member_names(names)
        return {"contract_version": CONTRACT_VERSION, "image": image, "scanned_members": len(names),
                "ok": ok, "findings": findings,
                "verdict": "CLEAN" if ok else "REJECTED_SECRET_CONTAMINATED"}
    finally:
        if cid:
            _docker(["rm", "-f", cid])


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: hermes_image_secret_scan_v1.py <image-ref>", file=sys.stderr)
        return 2
    res = scan_image(argv[0])
    print(json.dumps(res, sort_keys=True))
    return 0 if res["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
