#!/usr/bin/env bash
# WO-HERMES-UTC-AUDIT-FIX-0001 — UTC time-source discipline tripwire for HERMES.
#
# HERMES (tradingSignals) owns feed/candle/backfill timestamps — the platform
# evidence spine. This tripwire bans unsafe runtime/database current-time
# patterns across the entire repo.
#
# Banned patterns (Python files only):
#   datetime.utcnow()
#   bare datetime.now() (no timezone arg — timezone-naive)
#   date.today() (local-TZ dependent)
#
# Allowed only with an explicit annotation on the SAME LINE or the
# IMMEDIATELY PRECEDING comment line:
#
#     # UTC_AUDIT_METADATA_OK: <reason>
#     <line containing the unsafe pattern>
#
# The annotation reason must be NON-EMPTY (no lazy blanket whitelists).
#
# Tests under tests/ are skipped. Comment-only lines (where the pattern
# appears in a comment, not real code) are skipped.
#
# Companion to WO-PLATFORM-UTC-TIME-SOURCE-AUDIT-FIX-0001 (tradingProteus repo).

set -euo pipefail

CHECK_NAME="check-utc-time-source-discipline-hermes"
REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

EXCLUDE_PATH_SEGMENTS=(
  "/tests/"
  "/__pycache__/"
)

PY_BAD_PATTERNS_PCRE=(
  '\bdatetime\.utcnow\(\)'
  '\bdatetime\.now\(\)'
  '\bdate\.today\(\)'
)

fail_count=0
total_files=0

is_excluded() {
  local file="$1"
  for seg in "${EXCLUDE_PATH_SEGMENTS[@]}"; do
    if [[ "$file" == *"$seg"* ]]; then
      return 0
    fi
  done
  return 1
}

check_py_file() {
  local file="$1"
  local rel="${file#${REPO_ROOT}/}"

  if is_excluded "$file"; then
    return 0
  fi

  total_files=$((total_files + 1))

  local annotation_lines
  annotation_lines=$( { grep -n 'UTC_AUDIT_METADATA_OK' "$file" || true; } 2>/dev/null \
                     | awk -F: '{
                         line=$0;
                         pos=index(line, ":");
                         lineno=substr(line, 1, pos-1);
                         rest=substr(line, pos+1);
                         marker_pos=index(rest, "UTC_AUDIT_METADATA_OK:");
                         if (marker_pos > 0) {
                           reason=substr(rest, marker_pos + length("UTC_AUDIT_METADATA_OK:"));
                           sub(/^[ \t]+/, "", reason);
                           if (length(reason) > 0) print lineno;
                         }
                       }')

  local annot_set=":$(echo "$annotation_lines" | tr '\n' ':' )"

  for pat in "${PY_BAD_PATTERNS_PCRE[@]}"; do
    local matches
    matches=$({ grep -nP "$pat" "$file" || true; } 2>/dev/null)
    if [[ -z "$matches" ]]; then
      continue
    fi
    while IFS= read -r match_line; do
      [[ -z "$match_line" ]] && continue
      local lineno="${match_line%%:*}"
      if [[ "$annot_set" == *":${lineno}:"* ]]; then
        continue
      fi
      local prev=$((lineno - 1))
      if [[ "$annot_set" == *":${prev}:"* ]]; then
        continue
      fi
      if echo "$match_line" | grep -q 'UTC_AUDIT_METADATA_OK:'; then
        local rest_after_marker
        rest_after_marker=$(echo "$match_line" | awk -F'UTC_AUDIT_METADATA_OK:' '{print $2}')
        rest_after_marker=$(echo "$rest_after_marker" | sed 's/^[[:space:]]*//')
        if [[ -n "$rest_after_marker" ]]; then
          continue
        fi
      fi
      # Skip comment-only lines (pattern only appears in documentation).
      local code_part="${match_line#*:}"
      local stripped="${code_part#"${code_part%%[![:space:]]*}"}"
      if [[ "$stripped" == "#"* ]]; then
        continue
      fi
      echo "[${CHECK_NAME}] FAIL: ${rel}:${lineno}  pattern=${pat}  ${match_line}"
      fail_count=$((fail_count + 1))
    done <<< "$matches"
  done
}

echo "[${CHECK_NAME}] scanning HERMES repo for unsafe time-source patterns…"

while IFS= read -r -d '' file; do
  check_py_file "$file"
done < <(find "$REPO_ROOT" -type f -name '*.py' -print0)

echo "[${CHECK_NAME}] scanned ${total_files} python files in HERMES"

if (( fail_count > 0 )); then
  echo "[${CHECK_NAME}] FAIL: ${fail_count} unsafe time-source usage(s) without UTC_AUDIT_METADATA_OK annotation"
  echo "[${CHECK_NAME}] Add an explicit annotation comment with reason on the same or preceding line:"
  echo "    # UTC_AUDIT_METADATA_OK: <why this is audit metadata, not cycle/decision truth>"
  echo "    <line containing the pattern>"
  exit 1
fi

echo "[${CHECK_NAME}] PASS — all unsafe time-source usages in HERMES are annotated"
