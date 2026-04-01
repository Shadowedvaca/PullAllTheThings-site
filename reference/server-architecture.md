# Server Architecture

All environments follow the three-server pattern defined in `reference/git-cicd-workflow.md`.
Dev and test are shared CX23 nodes. Prod is per-project.

---

## Server Inventory

| Role | SSH Alias | IP | Region | Spec | Purpose |
|------|-----------|----|--------|------|---------|
| **Dev** | `my-web-apps-dev` | 91.99.112.160 | Falkenstein, DE | CX23 (2vCPU / 4GB) | Shared sandbox. All dev environments. |
| **Test** | `my-web-apps-test` | 91.99.121.21 | Falkenstein, DE | CX23 (2vCPU / 4GB) | Shared integration gate. All test environments. |
| **Prod (PATT)** | `hetzner` | 5.78.114.224 | Hillsboro, OR | CPX21 | Pull All The Things — live |

---

## Port Assignments (Shared Dev / Test Servers)

Each app occupies one port slot on the shared dev and test servers. Nginx routes by subdomain to the assigned port. The same port number is used on both servers.

| Port | App | Subdomain (dev) | Subdomain (test) | Status |
|------|-----|-----------------|------------------|--------|
| **8100** | Pull All The Things (PATT) | `dev.pullallthethings.com` | `test.pullallthethings.com` | Active |
| **8200** | _(open)_ | — | — | Available |
| **8300** | _(open)_ | — | — | Available |
| **8400** | _(open)_ | — | — | Available |
| **8500** | _(open)_ | — | — | Available |
| **8600** | _(open)_ | — | — | Available |
| **8700** | _(open)_ | — | — | Available |
| **8800** | _(open)_ | — | — | Available |
| **8900** | _(open)_ | — | — | Available |
| **9000** | _(open)_ | — | — | Available |

**Rules:**
- Each app claims one port; pick the next available slot in the table above and update this file
- Use the same port on both dev and test — keeps nginx config symmetric
- App's `docker-compose.dev.yml` / `docker-compose.test.yml` maps `PORT:8100` (host:container)
- Nginx vhost on each shared server proxies `subdomain → localhost:PORT`
- Prod servers are single-app — no port coordination needed there

---

## Per-App Deployment Layout

Each app on a shared server follows this pattern:

```
/opt/<app-name>/
├── .env                     # env vars for app + DB_PASSWORD for compose
├── docker-compose.dev.yml   # (or docker-compose.test.yml on test server)
└── ... (rest of repo)
```

The `docker-compose.dev.yml` / `docker-compose.test.yml` files live in the repo. The `.env` file is server-local (never committed).

---

## SSH Access

Mike's personal key and the shared GitHub Actions deploy key are installed on all three servers. SATT deploy key is also present on the shared servers for future use.

```bash
ssh my-web-apps-dev   # dev shared server
ssh my-web-apps-test  # test shared server
ssh hetzner           # PATT prod
```

---

*Last updated: 2026-04-01*
