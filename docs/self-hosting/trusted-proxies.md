---
# SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
# SPDX-License-Identifier: Apache-2.0
title: "Trusted Proxies & Network Security"
---

If you're behind a load balancer or reverse proxy, you MUST configure trusted proxies or rate limiting will break — all traffic will appear to come from a single IP (the proxy's), triggering limits for your entire deployment at once.

## Quick Setup

1. Go to **Settings > Network > Trusted Proxies**.
2. Paste your load balancer's internal IP or CIDR range.
3. Save.

**If you're using AWS ALB:**

```
10.0.0.0/8
```

(ALB forwards from within your VPC's private range.)

**If you're using nginx on the same host:**

```
127.0.0.1
```

**If you're using Cloudflare:**

Add Cloudflare's published IP ranges (see `https://www.cloudflare.com/ips/`):

```
173.245.48.0/20, 103.21.244.0/22, 103.22.200.0/22, 103.31.4.0/22, 141.101.64.0/18, 108.162.192.0/18, 190.93.240.0/20, 188.114.96.0/20, 197.234.240.0/22, 198.41.128.0/17
```

**If you're using a Kubernetes service mesh (Envoy/Istio):**

```
127.0.0.6/32, 10.0.0.0/8
```

## Verify it works

After saving, check that Observal resolves the correct client IP:

```bash
curl -s https://observal.yourcompany.com/api/health/ip \
  -H "Authorization: Bearer <your-token>"
```

Expected: Your actual public IP (not the proxy's IP). If you see the proxy's IP instead, the trusted proxy list doesn't include your proxy's address.

Also check the audit log: **Settings > Audit Log** should show distinct IPs for different users, not a single proxy IP for everyone.


## Field Reference

### Trusted Proxy IPs

A list of IP addresses or CIDR ranges that Observal trusts to set forwarding headers (`X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Proto`).

**Affects:** IP resolution for rate limiting, audit logs, and access controls. Only IPs in this list are trusted to pass `X-Forwarded-For`. Traffic from unlisted IPs uses the TCP source address directly, ignoring forwarding headers.

**Default:** `172.16.0.0/12, 10.0.0.0/8, 192.168.0.0/16, 127.0.0.1` (private networks and localhost)

**Format:** Comma-separated IPs or CIDR blocks.

**Values:**

| Value | Effect |
|-------|--------|
| `172.16.0.0/12, 10.0.0.0/8, 192.168.0.0/16, 127.0.0.1` (default) | Trusts all RFC 1918 private ranges; works for most k8s/docker setups |
| `10.0.0.0/8` | Only trust AWS VPC internal traffic (ALB, internal LBs) |
| `173.245.48.0/20, 103.21.244.0/22, ...` (Cloudflare ranges) | Trust Cloudflare proxy IPs for correct client IP extraction |
| `0.0.0.0/0` | **DANGEROUS** — trusts everyone; any client can spoof their IP |

**When to set:** When deploying behind any load balancer, reverse proxy, or CDN. Without correct configuration, all users appear as the proxy's IP in audit logs and share one rate-limit bucket.

**Security best practice:** Never add `0.0.0.0/0` (trust everything). This allows any client to spoof their apparent IP address.

**Symptom-to-fix:**

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| All users show same IP in audit logs | Proxy IP not trusted; Observal records proxy IP as client | Add proxy's IP to trusted list |
| Rate limits trigger for the whole office at once | Same as above — all traffic appears from one IP | Add proxy's IP to trusted list |
| Rate limits don't work at all | Trusted list is too broad (e.g., `0.0.0.0/0`) | Narrow to actual proxy IPs only |
| Users get `403 Untrusted proxy` | Client's IP is in a range Observal doesn't recognize | Check if a new proxy/LB was added without updating this list |


## SSRF Protection

Server-Side Request Forgery (SSRF) protection prevents Observal from making outbound requests to internal/private networks when processing user-supplied URLs (webhook destinations, IdP metadata URLs, custom API base URLs).

**Settings:**

| Field | Default | Purpose |
|-------|---------|---------|
| Enabled | `true` | Master toggle |
| Blocked ranges | RFC 1918 + link-local | Private IP ranges that outbound requests cannot target |
| Allow-listed hosts | — | Internal hosts exempt from SSRF blocking |
| DNS rebinding protection | `true` | Re-resolve DNS before connecting to catch rebinding attacks |

**Default blocked ranges:**
- `10.0.0.0/8`
- `172.16.0.0/12`
- `192.168.0.0/16`
- `169.254.0.0/16` (link-local / cloud metadata)
- `127.0.0.0/8` (loopback)
- `::1/128`, `fc00::/7`, `fe80::/10` (IPv6 equivalents)

**When to add allow-listed hosts:**
- Your IdP metadata URL is on an internal hostname (e.g., `idp.internal.yourcompany.com`).
- Webhooks need to reach an internal service (e.g., `slack-gateway.internal:8080`).

> **Warning:** Never disable SSRF protection in production. Use the allow-list for legitimate internal targets.

**Symptom-to-fix:**

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Webhook delivery fails with "SSRF blocked" | Destination resolves to a private IP | Add the host to the allow-list |
| IdP metadata fetch fails | Metadata URL is internal | Add IdP hostname to allow-list |
| "DNS rebinding detected" error | DNS returned a private IP on re-resolution | Legitimate if the host is internal — allow-list it; suspicious if the domain is external — investigate |

#### Allow Internal Git URLs

Whether agent and component definitions can reference Git repositories on private/internal networks.

**Affects:** Agent scaffolding and component installation from Git sources. When `false`, Git URLs that resolve to private IP ranges (RFC 1918) are blocked by SSRF protection. When `true`, internal Git repos are permitted.

**Values:**

| Value | Effect |
|-------|--------|
| `false` (default) | Git URLs pointing to private IPs are blocked; only public repos allowed |
| `true` | Internal Git repos (e.g., `git@gitlab.internal:org/repo.git`) are accessible for agent/component sources |

**When to set:** Your deployment hosts agent source code on an internal GitLab/Gitea/GitHub Enterprise instance that resolves to a private IP. Enable this so `observal publish` and component installs can clone from internal repos.
