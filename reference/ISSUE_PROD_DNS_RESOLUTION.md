# Issue: Hetzner Server Resolves pullallthethings.com to GitHub Pages IPs

## Status
Open — not yet root-caused

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

## Hypotheses to Investigate

1. **Hetzner server DNS cache.** The server's resolver (`/etc/resolv.conf`) may be
   caching old GitHub Pages A records. The repo previously used GitHub Pages. Check
   whether the Hetzner server's upstream DNS resolver has stale cached entries.
   ```bash
   cat /etc/resolv.conf
   resolvectl status
   # Try flushing systemd-resolved cache:
   resolvectl flush-caches
   # Re-check:
   dig +short pullallthethings.com
   ```

2. **GitHub Pages still configured somewhere.** GitHub Pages for this repo returns
   a 404 API response (not enabled), but GitHub may still be answering for the
   domain if it was previously configured — check the repo's Pages settings in
   GitHub UI directly, not just via API.

3. **Registrar propagation lag.** The user's DNS update may be correct but not yet
   propagated to the resolvers the Hetzner server uses. DNS TTL on the old GitHub
   Pages records may still be in effect on some resolvers.
   ```bash
   # Check from multiple resolvers:
   dig +short pullallthethings.com @8.8.8.8
   dig +short pullallthethings.com @1.1.1.1
   dig +short pullallthethings.com @9.9.9.9
   # If any return 5.78.114.224, propagation is partial
   ```

4. **Split-horizon DNS / local override needed.** Even if external DNS is correct,
   the server resolving its own domain name externally is fragile. A robust fix is
   an `/etc/hosts` entry so the server always routes the domain to itself:
   ```
   5.78.114.224    pullallthethings.com www.pullallthethings.com
   ```
   This is a good hardening step regardless of root cause.

## Immediate Workaround (Already Applied)

The CI/CD prod health check now uses `http://localhost:8100/api/health` instead of
`https://pullallthethings.com/api/health`. This bypasses the DNS issue entirely for
deployments. The smoke test in this issue document is a separate manual step and is
where the issue manifests.

## Recommended Fix

After root-causing, apply the `/etc/hosts` entry on the Hetzner server as a permanent
hardening measure so the server never relies on external DNS to reach itself.
Also update the prod smoke test in the deploy workflow (if added) to use localhost.
