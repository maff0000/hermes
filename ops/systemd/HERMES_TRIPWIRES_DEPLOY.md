# HERMES operational tripwires — deployment

WO-HELM-HERMES-DEV-PRE-PROD-RECOVERY-GATE-CLOSURE-0001.

Host-side, `systemd` timer. Not baked into the HERMES application image — no container/compose
change, no HERMES restart required to deploy or update this.

## Install (per host: DEV = dell-debian, PROD = HERMES VPS)

```bash
# 1. Substitute the checkout path for this host and install the units
sed "s#__HERMES_CHECKOUT_DIR__#/path/to/hermes/checkout#g" ops/systemd/hermes-tripwires.service \
  > /etc/systemd/system/hermes-tripwires.service
cp ops/systemd/hermes-tripwires.timer /etc/systemd/system/hermes-tripwires.timer

# 2. Ensure the checkout's .env carries (in addition to what HERMES itself already requires):
#      HERMES_TRIPWIRE_STATE_PATH=/var/lib/hermes/tripwire_state.json   (optional; has a default)
#      HERMES_TRIPWIRE_CONTAINERS=hermes-signal,hermes-cache            (optional; has a default)
#      HERMES_REDIS_CAPACITY_CEILING_BYTES=<bytes>                      (only if neither Redis
#                                                                         maxmemory nor a readable
#                                                                         cgroup limit exists)

systemctl daemon-reload
systemctl enable --now hermes-tripwires.timer
systemctl list-timers hermes-tripwires.timer   # confirm scheduled
```

## What it does

Every 5 minutes: reads Redis `INFO memory`, `docker inspect`s the configured containers, reads
`/etc/hostname` + `hostname`. Opens/closes incidents in the existing `hermes_incidents` table via
`utils.watchdog.HealthPersistence` — the same table `/health` already surfaces. Never restarts,
repairs, or mutates anything it inspects.

## What it does NOT do

- Does not run inside the HERMES container — a bad tripwire deploy can never take HERMES down.
- Does not auto-correct hostname drift, Redis capacity, or container restarts.
- Does not require `docker.sock` inside HERMES — `docker inspect` runs on the host directly.

## Removal

```bash
systemctl disable --now hermes-tripwires.timer
rm /etc/systemd/system/hermes-tripwires.{service,timer}
systemctl daemon-reload
```
