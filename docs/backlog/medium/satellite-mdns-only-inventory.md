# Retire `satellites.yaml` in Favour of mDNS Discovery

## Summary

Satellite deployment merges a hand-maintained `~/code/alfred-deploy/satellites.yaml` with
`avahi-browse -rpt _wyoming._tcp` discovery. The intended end state is discovery alone —
adding a Pi should mean flashing it, not editing a file on lath-server.

## Context / Motivation

- `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md` §7.3: "Both sources exist
  because the file is authoritative today while discovery earns trust. The intended end
  state is discovery alone; retiring the file is a ticket, not a TODO."
- `dev/satellites/inventory.py` in `alfred-satellite` already implements the union and
  marks each device's `source` as `file`, `mdns` or `both`. `resolve_fleet()` in
  `dev/deploy_satellites.py` logs a warning for any device that is not `both` — that log is
  the evidence this ticket waits on.
- The fleet is one device today (`pi1`), so there isn't yet enough rollout history to know
  whether discovery is reliable across reboots/DHCP changes.

## Blocked on

Several consecutive rollouts where every device resolves as `both`, across a fleet large
enough to say something meaningful. Until then a Pi that stops advertising would silently
drop out of the fleet, which is exactly what the file prevents.

## What retiring it means

- `area` has no mDNS equivalent and is needed for room-aware commands, so it has to move
  into the Pi's own config and be advertised as a TXT record before the file can go.
- `load_file` and `merge` stay useful for `--dry-run` against a hypothetical fleet; the
  deploy path stops consulting the file.
