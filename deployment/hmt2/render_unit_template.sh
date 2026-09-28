#!/usr/bin/env bash
# Renders one of this directory's systemd unit .template files against a host's config env
# file, using envsubst restricted to an explicit variable allowlist (never a blanket
# substitution) so nothing in the rendered unit is ever accidentally interpreted.
#
# Usage: render_unit_template.sh <env-file> <template-path> <output-path>
#
# Requires: envsubst (gettext-base package on Debian/Ubuntu -- already present on dell-debian
# and Trinity as of this dispatch).
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: render_unit_template.sh <env-file> <template-path> <output-path>" >&2
  exit 2
fi

env_file="$1"
template_path="$2"
output_path="$3"

if [ ! -f "$env_file" ]; then
  echo "render_unit_template.sh: env file not found: ${env_file}" >&2
  exit 1
fi
if [ ! -f "$template_path" ]; then
  echo "render_unit_template.sh: template not found: ${template_path}" >&2
  exit 1
fi

# `set -a`: every variable the env file sets is automatically exported, so envsubst (a separate
# process) can see it -- a plain `source` alone only sets shell-local variables, never exported
# ones, and envsubst would otherwise substitute everything as empty.
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

# Explicit allowlist of substitution variables -- see deployment/hmt2/hmt2-ops.env.example for
# what each one means. Adding a new templated value requires adding it here explicitly.
export HMT2_OPS_ENV_FILE_DISPLAY="${HMT2_OPS_ENV_FILE_DISPLAY:-$env_file}"
allowlist='$HMT2_SLICE_MEMORY_HIGH,$HMT2_SLICE_MEMORY_MAX,$HMT2_SLICE_CPU_QUOTA,$HMT2_SLICE_IO_WEIGHT,$HMT2_DISK_GUARD_INTERVAL_MIN,$HMT2_DISK_GUARD_SCRIPT_PATH,$HMT2_OPS_ENV_FILE_DISPLAY'

envsubst "$allowlist" < "$template_path" > "$output_path"
echo "rendered ${template_path} -> ${output_path}"
