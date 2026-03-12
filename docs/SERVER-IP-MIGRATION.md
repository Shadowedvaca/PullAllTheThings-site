# Server IP Migration Guide

> **Read this before changing the server IP, migrating to a new server, or changing DNS records.**
> This doc exists because a specific DNS/routing issue burned us once and will burn us again
> if we don't account for it consciously.

---

## The Problem: Server Resolving Its Own Domain via External DNS

### What happened

During the v0.0.2 production smoke test, `curl https://pullallthethings.com/api/health`
run **from the Hetzner server itself** returned a 404. The server was connecting to
`185.199.110.153` (a GitHub Pages IP) instead of its own IP (`5.78.114.224`).

This repo previously used GitHub Pages for hosting. When we migrated to Hetzner and
updated the DNS A record, most resolvers propagated quickly — but **Google DNS (8.8.8.8)
retained stale GitHub Pages A records for an extended period** (still stale more than
24h after the DNS update was confirmed correct at the registrar).

The Hetzner server's `systemd-resolved` uses Hetzner's own resolvers (`185.12.64.x`)
as `+DefaultRoute` link resolvers, which had the correct record. But during the window
when Google's resolver was stale, a cached lookup via `8.8.8.8` caused the server to
route its own domain to GitHub's servers.

The symptom: the app and database are healthy, nginx is configured correctly, the SSL
cert is valid — but `curl https://pullallthethings.com/` from the server connects to
the wrong IP and gets a GitHub 404.

### Why this matters

Any time the server needs to call itself by domain name (health checks, webhooks,
internal redirects, CI/CD smoke tests), it must resolve the domain to its own IP.
External DNS propagation is not instantaneous and is not under our control. Google DNS
in particular has been observed serving stale records for this domain for 24+ hours
after a correct update at the registrar.

---

## The Fix: `/etc/hosts` Override

The permanent hardening is an `/etc/hosts` entry that forces the server to always
route its own domain to itself, bypassing external DNS entirely:

```
5.78.114.224    pullallthethings.com www.pullallthethings.com
```

### How the entry is managed

The Hetzner server runs `cloud-init` with `manage_etc_hosts: True`. This means
`/etc/hosts` is **regenerated from a template on every boot**. A direct edit to
`/etc/hosts` will survive until the next reboot, then disappear.

The entry must be in **both** places:

**1. `/etc/hosts`** — takes effect immediately:
```bash
echo '5.78.114.224    pullallthethings.com www.pullallthethings.com' >> /etc/hosts
```

**2. `/etc/cloud/templates/hosts.debian.tmpl`** — survives reboots:
```bash
cat >> /etc/cloud/templates/hosts.debian.tmpl << 'EOF'

# Guild platform - force local resolution (avoids external DNS dependency)
5.78.114.224    pullallthethings.com www.pullallthethings.com
EOF
```

Verify:
```bash
grep pullall /etc/hosts
# 5.78.114.224    pullallthethings.com www.pullallthethings.com

curl -sf https://pullallthethings.com/api/health
# {"ok":true,"data":{"db":"connected","version":"0.1.0"}}

curl -sv https://pullallthethings.com/ 2>&1 | grep Connected
# * Connected to pullallthethings.com (5.78.114.224) port 443
```

---

## Checklist: Changing the Server IP

If you are migrating to a new server or reassigning the Hetzner IP, do **all** of
the following or the issue will recur.

### 1. Update the registrar DNS A record

Point `pullallthethings.com` and `www.pullallthethings.com` (and `dev.` and `test.`
subdomains if applicable) to the new IP. Do this first to start the propagation clock.

### 2. Update the `/etc/hosts` override on the new server

When you provision the new server, add the `/etc/hosts` override immediately — before
you run any smoke tests or health checks. Do not wait for DNS propagation.

```bash
# Replace NEW_IP with the actual new server IP
NEW_IP="x.x.x.x"

echo "${NEW_IP}    pullallthethings.com www.pullallthethings.com" >> /etc/hosts

cat >> /etc/cloud/templates/hosts.debian.tmpl << EOF

# Guild platform - force local resolution (avoids external DNS dependency)
${NEW_IP}    pullallthethings.com www.pullallthethings.com
EOF
```

### 3. Update the old server's `/etc/hosts` entry (or decommission it)

If the old server is being decommissioned: no action needed.

If both servers will run temporarily in parallel: update the old server's `/etc/hosts`
to point to the **new** IP as well, so cross-server requests don't loop back to the
wrong place.

If keeping the old server for another purpose: remove or update the `pullallthethings.com`
entry in both `/etc/hosts` and the cloud-init template.

### 4. Update CLAUDE.md

The server IP appears in the `Architecture` section and the `Server Access` memory
note. Update both:
- `CLAUDE.md` → Architecture block (`Hetzner Server (x.x.x.x)`)
- `memory/MEMORY.md` and the `user` memory file → SSH alias target

### 5. Update the `~/.ssh/config` alias on your local machine

```
Host hetzner
  HostName NEW_IP
  User root
  IdentityFile ~/.ssh/your_key
```

### 6. Update the GitHub Actions deploy secret if the SSH key changes

If the new server uses a different SSH keypair, update `DEPLOY_SSH_KEY` in the
GitHub repo secrets.

### 7. Verify DNS propagation from multiple resolvers before removing the old server

```bash
dig +short pullallthethings.com @8.8.8.8    # Google — often slowest to propagate
dig +short pullallthethings.com @1.1.1.1    # Cloudflare — usually fast
dig +short pullallthethings.com @9.9.9.9    # Quad9
```

All three should return the new IP before you decommission the old server. **Do not
trust that Google's resolver has propagated just because Cloudflare has.** We have
observed 8.8.8.8 serving stale records 24+ hours after a correct registrar update.

### 8. Transfer the SSL certificate or reissue it

If using Let's Encrypt (Certbot/Caddy), the new server needs a cert issued against
the new IP. Caddy handles this automatically on first startup if the domain resolves
to the server. Certbot requires running `certbot certonly` or `certbot renew`.

The cert challenge requires the domain to resolve publicly to the new server's IP —
so do not run cert issuance until propagation is sufficient (1.1.1.1 and 9.9.9.9
returning the new IP is generally sufficient, even if 8.8.8.8 is still stale, because
the ACME challenge uses multiple vantage points).

### 9. Run the full smoke test from the new server

```bash
# From the new server via SSH:
curl -sf https://pullallthethings.com/api/health
curl -sv https://pullallthethings.com/ 2>&1 | grep Connected
# Must show: * Connected to pullallthethings.com (NEW_IP) port 443
```

If `Connected` shows the old IP or a GitHub IP: check `/etc/hosts` first (step 2).

---

## Diagnosing the Issue If It Recurs

```bash
# 1. What IP is the server resolving the domain to?
dig +short pullallthethings.com

# 2. Compare against multiple external resolvers
dig +short pullallthethings.com @8.8.8.8
dig +short pullallthethings.com @1.1.1.1
dig +short pullallthethings.com @9.9.9.9

# 3. What resolver is systemd-resolved actually using?
resolvectl status | head -20

# 4. Is there an /etc/hosts entry? Is it correct?
grep pullall /etc/hosts

# 5. Does the app work when bypassing DNS?
curl -sf https://pullallthethings.com/api/health \
  --resolve pullallthethings.com:443:$(hostname -I | awk '{print $1}')

# 6. Is GitHub Pages still configured on the repo?
gh api repos/Shadowedvaca/PullAllTheThings-site/pages
# Should return 404. If it returns data, disable GitHub Pages in repo Settings.
```

---

## Background: Why This Happens with GitHub Pages

When a repo is configured for GitHub Pages with a custom domain, GitHub registers
the custom domain in their IP space. When you remove GitHub Pages, GitHub removes
the domain mapping — but **DNS caches across the internet retain the old A records
until TTL expiry**, and some resolvers (especially Google) appear to serve stale
records significantly longer than the stated TTL.

The `/etc/hosts` fix is the correct permanent solution because it removes the
dependency on external DNS for the server's self-resolution entirely. No amount of
DNS propagation debugging matters once the `/etc/hosts` entry is in place.
