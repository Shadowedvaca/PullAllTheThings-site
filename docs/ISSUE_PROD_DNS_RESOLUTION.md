# Issue: Hetzner Server Resolves pullallthethings.com to GitHub Pages IPs

## Status
**Resolved** — root-caused and hardened (2026-03-11)

## What Happened

During the v0.0.2 prod smoke test, `curl https://pullallthethings.com/api/health` run
**from the Hetzner server itself** (via SSH) returned a 404. Investigation showed the
server was connecting to `185.199.110.153` — a GitHub Pages IP — instead of itself
(`5.78.114.224`).

```bash
# Run on Hetzner server:
dig +short pullallthethings.com
# 185.199.110.153
# 185.199.108.153
# 185.199.109.153
# 185.199.111.153

curl -sv https://pullallthethings.com/ 2>&1 | grep Connected
# * Connected to pullallthethings.com (185.199.110.153) port 443
```

When forced to connect to the Hetzner IP directly, the app responds correctly:
```bash
curl -sf https://pullallthethings.com/api/health \
  --resolve pullallthethings.com:443:5.78.114.224
# {"ok":true,"db":"connected"}
```

## What Is NOT the Issue

- **Not the user's DNS registrar.** The user confirmed their DNS records are correct.
  Dev and test subdomains (`dev.`, `test.`) were added at the same registrar session
  and resolve correctly to `5.78.114.224`.
- **Not Chrome socket caching.** CLAUDE.md documents a Chrome-specific quirk
  (flush `chrome://net-internals/#sockets`). This is unrelated — the problem occurs
  server-side in curl/SSH, not in a browser.
- **Not the CI/CD health check.** The deploy workflow was already fixed to use
  `http://localhost:8100/api/health` (bypasses DNS entirely). CI/CD passes.
- **Not the app itself.** `curl http://localhost:8100/api/health` returns healthy.
  Nginx is configured correctly and the SSL cert exists on the server.
- **Not GitHub Pages being active.** `GET /repos/Shadowedvaca/PullAllTheThings-site/pages`
  returns 404 — GitHub Pages is not configured on this repo.

## Root Cause

**Partial DNS propagation + Google DNS caching stale GitHub Pages A records.**

The server uses `systemd-resolved` (`/etc/resolv.conf` → `127.0.0.53` stub).
`resolvectl status` shows two sets of resolvers:

- **eth0 link resolvers** (Hetzner's): `185.12.64.2`, `185.12.64.1` (+ IPv6 equivalents)
  — marked `+DefaultRoute`, so used for all general DNS lookups
- **Global resolvers**: `8.8.8.8`, `1.1.1.1` (fallback)

At the time of the incident, systemd-resolved had a cached stale result (GitHub Pages IPs)
from a prior lookup that went through 8.8.8.8. Google DNS (8.8.8.8) still has stale records
even after investigation — it appears Google's resolver had a longer TTL window for the old
GitHub Pages A records. Confirmed resolver results at time of investigation:

```
dig +short pullallthethings.com               → 5.78.114.224   ✓ (via Hetzner resolver)
dig +short pullallthethings.com @8.8.8.8      → 185.199.x.x    ✗ (still stale!)
dig +short pullallthethings.com @1.1.1.1      → 5.78.114.224   ✓
dig +short pullallthethings.com @9.9.9.9      → 5.78.114.224   ✓
dig +short pullallthethings.com @185.12.64.2  → 5.78.114.224   ✓
```

The issue resolved itself once systemd-resolved's stale cache expired and queries began
going through Hetzner's link resolvers (which have the correct records). However, the
server remains vulnerable to this scenario any time 8.8.8.8 is used.

## Immediate Workaround (Already Applied)

The CI/CD prod health check now uses `http://localhost:8100/api/health` instead of
`https://pullallthethings.com/api/health`. This bypasses the DNS issue entirely for
deployments.

## Fix Applied (2026-03-11)

Added `/etc/hosts` entry on the Hetzner server to force local resolution, bypassing
external DNS entirely when the server resolves its own domain:

```
5.78.114.224    pullallthethings.com www.pullallthethings.com
```

Because the server runs `cloud-init` with `manage_etc_hosts: True`, a direct edit to
`/etc/hosts` will be overwritten on next boot. The entry was added to **both**:

1. `/etc/hosts` — immediate effect
2. `/etc/cloud/templates/hosts.debian.tmpl` — persists across reboots/cloud-init runs

Post-fix smoke test passes:
```bash
curl -sf https://pullallthethings.com/api/health
# {"ok":true,"data":{"db":"connected","version":"0.1.0"}}

curl -sv https://pullallthethings.com/ 2>&1 | grep Connected
# * Connected to pullallthethings.com (5.78.114.224) port 443
```

The server will now always route `pullallthethings.com` to itself regardless of what
any external DNS resolver returns.
