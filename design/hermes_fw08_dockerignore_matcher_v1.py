#!/usr/bin/env python3
"""HERMES FW-08 Docker-faithful .dockerignore matcher v1 (PURE).

WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001.
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

WHY THIS EXISTS (F-113-03). PR#113's `tools/hermes_clean_build_context_v1.py` carries a *homemade*
.dockerignore matcher used to summarise excluded categories as an INDEPENDENT DEFENCE. R2D2 finding
F-113-03 is that a homemade matcher is NOT admissible as the Stage-B PROOF of the effective build
context, because it was never validated against Docker/BuildKit semantics for `**`, rooted patterns,
directory patterns and negation (`!`). This module is a well-validated, parity-fixtured re-implementation
of Docker/Moby `patternmatcher` semantics (github.com/moby/patternmatcher) so the wrapper can establish
the ACTUAL file set that Docker would place into the image AFTER .dockerignore — deterministically and
faithfully — WITHOUT running a real `docker build`.

FIDELITY NOTES (mirrors Moby `patternmatcher`):
  * Each raw line is trimmed; blank lines and `#` comments are ignored.
  * A leading `!` marks an EXCLUSION (negation / re-include). It is stripped, then the remainder trimmed.
  * The remaining pattern is `filepath.Clean`-equivalent normalised (posix), and a single leading `/`
    (len > 1) is stripped — Docker anchors every pattern at the context ROOT (there is NO gitignore-style
    "match in any subdirectory"; that is what `**` is for).
  * Pattern -> regex: `**` at end -> `.*`; `**`/`**/` elsewhere -> `(.*<sep>)?`; single `*` -> `[^/]*`;
    `?` -> `[^/]`; regexp metacharacters are escaped; `/` is the separator.
  * A path is matched if the pattern matches the FULL path OR any of its parent-directory prefixes
    (so excluding a directory excludes its contents). The LAST matching pattern wins (an exclusion
    pattern re-includes).

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass
from typing import List, Sequence, Tuple

CONTRACT_VERSION = "1"
_SEP = "/"
# Regexp metacharacters that carry no meaning in a glob and so must be escaped (mirrors Moby shouldEscape).
_SHOULD_ESCAPE = set(".$+()|{}^")


@dataclass(frozen=True)
class DockerignorePattern:
    """One compiled .dockerignore pattern. `exclusion` True == a `!` re-include rule."""

    raw: str
    cleaned: str
    exclusion: bool
    regex: "re.Pattern[str]"

    def matches(self, path: str) -> bool:
        """Docker semantics: the pattern matches `path` if it matches the full path OR any parent
        directory prefix of the path."""
        if self.regex.match(path):
            return True
        # Parent-prefix matching (excluding a dir excludes its contents).
        parts = path.split(_SEP)
        for i in range(1, len(parts)):
            if self.regex.match(_SEP.join(parts[:i])):
                return True
        return False


def _clean_pattern(pattern: str) -> str:
    """posix `filepath.Clean` equivalent + single leading-slash strip (Docker `dockerignore.ReadAll`)."""
    cleaned = posixpath.normpath(pattern)
    # posixpath.normpath("") == "." and normpath(".") == "."; Docker skips empty, keeps "." meaningless.
    if len(cleaned) > 1 and cleaned.startswith(_SEP):
        cleaned = cleaned[1:]
    return cleaned


def _pattern_to_regex(cleaned: str) -> "re.Pattern[str]":
    """Compile a cleaned .dockerignore pattern to an anchored regex mirroring Moby `Pattern.compile`."""
    out: List[str] = ["^"]
    i = 0
    n = len(cleaned)
    while i < n:
        ch = cleaned[i]
        if ch == "*":
            if i + 1 < n and cleaned[i + 1] == "*":
                # some flavour of "**"
                i += 1
                # treat "**/" as "**" — consume the following separator if present
                if i + 1 < n and cleaned[i + 1] == _SEP:
                    i += 1
                if i + 1 == n:
                    # "**" at end of pattern — accept everything below (gitignore-parity)
                    out.append(".*")
                else:
                    # "**" in the middle — any number of path segments (including zero)
                    out.append("(.*" + _SEP + ")?")
            else:
                # single "*" — anything but the separator
                out.append("[^" + _SEP + "]*")
        elif ch == "?":
            out.append("[^" + _SEP + "]")
        elif ch in _SHOULD_ESCAPE:
            out.append("\\" + ch)
        elif ch == "\\":
            # escape the next char (a trailing backslash escapes itself)
            if i + 1 < n:
                out.append("\\" + cleaned[i + 1])
                i += 1
            else:
                out.append("\\\\")
        else:
            out.append(ch)
        i += 1
    out.append("$")
    return re.compile("".join(out))


def parse_dockerignore(text: str) -> Tuple[DockerignorePattern, ...]:
    """Parse .dockerignore text into ordered compiled patterns (Docker-faithful)."""
    patterns: List[DockerignorePattern] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        exclusion = line.startswith("!")
        body = line[1:].strip() if exclusion else line
        if not body:
            continue
        cleaned = _clean_pattern(body)
        if not cleaned or cleaned == ".":
            continue
        patterns.append(
            DockerignorePattern(
                raw=raw, cleaned=cleaned, exclusion=exclusion, regex=_pattern_to_regex(cleaned)
            )
        )
    return tuple(patterns)


def path_excluded(path: str, patterns: Sequence[DockerignorePattern]) -> bool:
    """Return True if Docker would EXCLUDE `path` from the build context. Last matching pattern wins;
    an exclusion (`!`) pattern re-includes."""
    matched = False
    for pat in patterns:
        if pat.matches(path):
            # Last matching pattern wins; an exclusion (`!`) pattern re-includes.
            matched = not pat.exclusion
    return matched


def partition(paths: Sequence[str], text: str) -> Tuple[List[str], List[str]]:
    """Return (included, excluded) partition of `paths` under a .dockerignore `text`."""
    patterns = parse_dockerignore(text)
    included: List[str] = []
    excluded: List[str] = []
    for p in paths:
        (excluded if path_excluded(p, patterns) else included).append(p)
    return sorted(included), sorted(excluded)


def effective_context(paths: Sequence[str], text: str) -> List[str]:
    """The ACTUAL file set Docker would place into the image after .dockerignore (sorted)."""
    included, _excluded = partition(paths, text)
    return included


def effective_context_checksum(included: Sequence[str]) -> str:
    """Deterministic checksum over the sorted effective-context path set."""
    core = json.dumps(sorted(included), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(core.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- parity fixtures (F-113-03)
@dataclass(frozen=True)
class ParityCase:
    """One Docker-parity assertion: under `patterns`, `path` is expected excluded==`excluded`."""

    label: str
    patterns: Tuple[str, ...]
    path: str
    excluded: bool


# Each case has been reasoned against Moby `patternmatcher` semantics. They cover `**`, rooted
# patterns, directory patterns and negation — the exact classes R2D2 F-113-03 requires validated.
PARITY_CASES: Tuple[ParityCase, ...] = (
    # --- single-star is NOT recursive (the classic Docker gotcha) ---
    ParityCase("star-suffix-toplevel", ("*.jsonl",), "notes.jsonl", True),
    ParityCase("star-suffix-nested-NOT-matched", ("*.jsonl",), "a/b/c.jsonl", False),
    # --- double-star IS recursive ---
    ParityCase("doublestar-prefix-nested", ("**/*.jsonl",), "a/b/c.jsonl", True),
    ParityCase("doublestar-prefix-toplevel", ("**/*.jsonl",), "c.jsonl", True),
    ParityCase("doublestar-alone-matches-all", ("**",), "any/deep/file", True),
    ParityCase("doublestar-mid", ("a/**/z",), "a/b/c/z", True),
    ParityCase("doublestar-mid-zero-dirs", ("a/**/z",), "a/z", True),
    # --- rooted patterns anchor at context root (no subdir matching) ---
    ParityCase("rooted-dir-toplevel", ("tests",), "tests/test_x.py", True),
    ParityCase("rooted-dir-not-in-subdir", ("tests",), "foo/tests/test_x.py", False),
    ParityCase("rooted-leading-slash-stripped", ("/tests",), "tests/x.py", True),
    ParityCase("rooted-path-exact", ("foo/bar",), "foo/bar", True),
    ParityCase("rooted-path-not-elsewhere", ("foo/bar",), "x/foo/bar", False),
    # --- directory patterns (trailing slash cleaned) exclude their contents via parent match ---
    ParityCase("dir-trailing-slash-contents", ("ops/evidence/",), "ops/evidence/WO/x.md", True),
    ParityCase("dir-doublestar-nested-claude", ("**/.claude",), "utils/.claude/settings.json", True),
    ParityCase("dir-plain-claude-not-nested", (".claude",), "utils/.claude/x", False),
    # --- negation / re-include (last match wins) ---
    ParityCase("negation-reinclude", ("*.md", "!README.md"), "README.md", False),
    ParityCase("negation-other-still-excluded", ("*.md", "!README.md"), "CHANGES.md", True),
    ParityCase("env-family-reinclude-example", (".env", ".env.*", "!.env.example"), ".env.example", False),
    ParityCase("env-family-excludes-env", (".env", ".env.*", "!.env.example"), ".env", True),
    ParityCase("env-family-excludes-env-prod", (".env", ".env.*", "!.env.example"), ".env.production", True),
    # --- question-mark is single non-separator ---
    ParityCase("qmark-single", ("file?.txt",), "fileA.txt", True),
    ParityCase("qmark-no-cross-sep", ("file?txt",), "file/txt", False),
    # --- ordering: a later inclusion can re-exclude a negated file ---
    ParityCase("reexclude-after-negation", ("*.log", "!keep.log", "keep.log"), "keep.log", True),
)


def run_parity_cases() -> List[Tuple[ParityCase, bool]]:
    """Evaluate every parity case; return (case, actual_excluded). Pure — for tests/evidence."""
    results: List[Tuple[ParityCase, bool]] = []
    for case in PARITY_CASES:
        text = "\n".join(case.patterns) + "\n"
        patterns = parse_dockerignore(text)
        results.append((case, path_excluded(case.path, patterns)))
    return results
