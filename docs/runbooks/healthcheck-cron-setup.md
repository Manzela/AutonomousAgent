# Healthcheck Cron Setup

Registers a host-level cron entry that pings Healthchecks.io every 5 minutes
based on `hermes-agent` container health. The script is `scripts/healthcheck-ping.sh`
(see Phase-1 plan T30 / T31).

## Prerequisites

- `secrets/healthchecks-url.sops` exists and decrypts (Phase-1 T30 user step;
  see `secrets/healthchecks-url.template.txt` for the procedure).
- The repo is checked out at `~/RX-Research Project/AutonomousAgent` (the cron
  line below uses an absolute path; adjust if your checkout lives elsewhere).
- `crontab` is available (built into macOS / most Linux distros).

## Steps

1. Create the host log directory (gitignored):

   ```bash
   mkdir -p logs
   ```

2. Register the cron entry (idempotent — strips any prior copy first):

   ```bash
   ( crontab -l 2>/dev/null | grep -v "AutonomousAgent.*healthcheck-ping" ; \
     echo "*/5 * * * * cd '/Users/danielmanzela/RX-Research Project/AutonomousAgent' && ./scripts/healthcheck-ping.sh >> logs/healthcheck.log 2>&1" \
   ) | crontab -
   ```

3. Verify it registered:

   ```bash
   crontab -l | grep healthcheck-ping
   ```

   Expected: the cron line appears.

## Notes for Linux hosts

- macOS may prompt for "Full Disk Access" the first time `cron` invokes the
  script. Approve it under System Settings -> Privacy & Security.
- On Linux, ensure `cron` (or `cronie`) is enabled and running:
  `systemctl enable --now cron`.

## Removing the entry

```bash
crontab -l | grep -v "AutonomousAgent.*healthcheck-ping" | crontab -
```

## Why `.gitignore` lists `logs/`

The cron job appends to `logs/healthcheck.log`. That file is host-local and
must never be committed (it would constantly produce diff noise). The repo
root `.gitignore` already includes `logs/`.
