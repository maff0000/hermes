# HMT-2 deployment -- launchers, disk guard, systemd units

This directory holds the DEPLOYABLE artefacts for the HMT-2 governed canonical-dispatch
mechanism ported from the Trinity operational delta (sealed HELM handoff bundle, 2026-09-27).
Nothing here is imported by application code; these are installed onto a host's filesystem by an
operator (or a future, separate provisioning WO -- explicitly out of scope for this dispatch, see
its "Explicit exclusions").

No file in this directory contains a real secret value. `hmt2-ops.env.example` contains only a
secret PATH (`HMT2_OPS_ACQUIRE_SECRET_PATH` / `HMT2_ACQUIRE_SECRET_PATH`), never the credential
itself.

## What's here

| File | Installs to | Owner:mode | Purpose |
|---|---|---|---|
| `hmt2-run.sh` | `/usr/local/sbin/hmt2-run.sh` | `root:root 0755` | Canonical-compute launcher (single systemd-run boundary, per session). |
| `hmt2-acquire-run.sh` | `/usr/local/sbin/hmt2-acquire-run.sh` | `root:root 0750` | Acquisition launcher (separate systemd-run boundary, secret-bearing). |
| `hmt2-disk-guard.sh` | `/usr/local/sbin/hmt2-disk-guard.sh` | `root:root 0755` | Independent, timer-driven disk-capacity early warning. |
| `systemd/hmt2.slice.template` | `/etc/systemd/system/hmt2.slice` (rendered) | `root:root 0644` | cgroup v2 containment slice. |
| `systemd/hmt2-disk-guard.service.template` | `/etc/systemd/system/hmt2-disk-guard.service` (rendered) | `root:root 0644` | Oneshot disk-guard service. |
| `systemd/hmt2-disk-guard.timer.template` | `/etc/systemd/system/hmt2-disk-guard.timer` (rendered) | `root:root 0644` | Runs the disk guard every N minutes. |
| `render_unit_template.sh` | (run by hand, not installed) | -- | Renders a `.template` against `hmt2-ops.env` via `envsubst`, allowlisted. |
| `hmt2-ops.env.example` | `/etc/hmt2/hmt2-ops.env` (copy + edit) | `root:root 0640` | The one config file every script/unit above reads. |

## Configuration doctrine

The three shell launchers (`hmt2-run.sh`, `hmt2-acquire-run.sh`, `hmt2-disk-guard.sh`) are
byte-for-byte identical across every host -- they contain no Trinity-specific or dell-debian-
specific value anywhere. They `source` `/etc/hmt2/hmt2-ops.env` (overridable via
`HMT2_OPS_ENV_FILE` for testing) at startup and refuse (via bash's `${VAR:?message}`) to run if a
required value is missing, rather than silently falling back to some other host's value.

The three systemd unit files CANNOT `source` a shell env file (systemd unit syntax has its own,
much more limited substitution model), so they are shipped as `.template` files with `${VAR}`
placeholders and rendered per host via `render_unit_template.sh <env-file> <template> <output>`,
which uses `envsubst` restricted to an explicit variable allowlist (never a blanket
substitution, so nothing else in the unit is ever accidentally interpreted). Re-render and
`systemctl daemon-reload` after any change to the routine envelope values in `hmt2-ops.env`.

`market_truth.acquisition.orchestration.config.Hmt2OpsConfig` (Python side, used by the
orchestrator itself) reads the SAME conceptual values from `os.environ`, using its own
`HMT2_OPS_*` variable names (see that module's `ENV_*` constants) -- if you want the Python
orchestrator's config and these shell/unit files to agree, export the same values into both
(e.g. via a systemd `EnvironmentFile=/etc/hmt2/hmt2-ops.env` on whatever wraps the orchestrator's
own invocation -- remembering that the orchestrator itself must still be launched directly as
root, never through `hmt2-run.sh`; see `canonical_root_orchestrator.py`'s module docstring for
why).

## Install (illustrative; not automated by this dispatch)

```bash
install -o root -g root -m 0755 deployment/hmt2/hmt2-run.sh /usr/local/sbin/hmt2-run.sh
install -o root -g root -m 0750 deployment/hmt2/hmt2-acquire-run.sh /usr/local/sbin/hmt2-acquire-run.sh
install -o root -g root -m 0755 deployment/hmt2/hmt2-disk-guard.sh /usr/local/sbin/hmt2-disk-guard.sh

mkdir -p /etc/hmt2
install -o root -g root -m 0640 deployment/hmt2/hmt2-ops.env.example /etc/hmt2/hmt2-ops.env
# edit /etc/hmt2/hmt2-ops.env with this host's real values

deployment/hmt2/render_unit_template.sh /etc/hmt2/hmt2-ops.env \
    deployment/hmt2/systemd/hmt2.slice.template /etc/systemd/system/hmt2.slice
deployment/hmt2/render_unit_template.sh /etc/hmt2/hmt2-ops.env \
    deployment/hmt2/systemd/hmt2-disk-guard.service.template /etc/systemd/system/hmt2-disk-guard.service
deployment/hmt2/render_unit_template.sh /etc/hmt2/hmt2-ops.env \
    deployment/hmt2/systemd/hmt2-disk-guard.timer.template /etc/systemd/system/hmt2-disk-guard.timer
chmod 0644 /etc/systemd/system/hmt2.slice /etc/systemd/system/hmt2-disk-guard.service /etc/systemd/system/hmt2-disk-guard.timer

systemctl daemon-reload
systemctl enable --now hmt2-disk-guard.timer
```

This dispatch does NOT run any of the above against dell-debian or Trinity -- see the governing
WO's "Explicit exclusions": no runtime deployment/proof in this dispatch, that is separate,
later work after independent audit.

## Privilege model this deployment depends on (host-provisioning, out of scope here)

The two launchers assume the following already exist on the host (provisioning is a separate,
future WO):

  * `hmt2.slice` exists (rendered from the template above).
  * A `hmt-compute`-equivalent identity (canonical compute) and a `hmt-data`-equivalent identity
    (acquisition) exist, non-overlapping, neither granted generic `systemd-run` authority (no
    polkit rule, no sudo, no linger for either).
  * A `hmt-acquire-secrets`-equivalent group exists, whose SOLE member is the acquisition
    identity; the secret file/directory is owned `root:<that group> 0750`/`0440` so the
    canonical compute identity cannot even `ls` the secret's directory.

See `docs/architecture/hmt0-market-truth-v2/hmt2-trinity-operational-delta-obsolete-notes.md`
for the anti-pattern (nested `systemd-run`) this deployment structurally avoids, and
`market_truth/acquisition/orchestration/canonical_root_orchestrator.py`'s module docstring for
the corrected topology.
