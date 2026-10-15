# Production edge boundary

This directory separates the unauthenticated product surface from the one public
Slack callback. It is a configuration foundation, not evidence that a real VPN,
firewall, TLS endpoint, DNS name, or Slack workspace has been installed.

## Listener contract

| Listener | Publish to | Routes | Default |
|---|---|---|---|
| `internal.conf` / `8080` | office/VPN interface or private load balancer only | `/`, `/api/*`, internal `/health` | source CIDR ACL, then deny |
| `slack-http.conf` / `8081` | public TLS edge only in `TC_SLACK_MODE=http` | exact `POST /integrations/slack/events` | every other route denied |

Do not publish the Web, API, worker, PostgreSQL, object-storage, or model ports.
In Socket Mode, do not publish `8081` at all. Firewall/LB rules must repeat this
separation; the Nginx ACL is defense in depth and not a replacement for them.

## Required configuration files

The listener deliberately fails `nginx -t` if one of these deployment-specific
files is missing:

1. Copy `internal-allowlist.conf.example` to
   `/etc/nginx/tc/internal-allowlist.conf`. Replace the documentation CIDRs with
   exact office/VPN egress CIDRs and retain the final `deny all;`. Do not allow
   all RFC1918 addresses just because they are private ranges.
2. Copy `trusted-proxies.conf.example` to
   `/etc/nginx/tc/trusted-proxies.conf`. Leave it without `set_real_ip_from`
   entries when Nginx accepts clients directly. Behind a load balancer, add only
   that balancer's exact source CIDRs. Never trust `0.0.0.0/0`, `::/0`, or an
   unverified forwarding tier.
3. Copy `internal-security-headers.conf.example` to
   `/etc/nginx/tc/internal-security-headers.conf`. Add the exact deployed private
   R2 browser origin to `connect-src` before direct browser PUT is enabled.
   Keeping the baseline blocks cross-origin upload rather than broadening CSP to
   `https:` or `*`.
4. Load `00-security-common.conf` before both listener files in Nginx's `http`
   context. It defines the redacted log format, real-IP trust, content-type map,
   and Slack rate/connection zones.

TLS must be enforced at the public/private edge. The tracked HSTS headers reach
the client only if the TLS terminator preserves them. Validate the deployed CSP
against the production Web bundle and exact R2 CORS origin before release.

## Forwarded-address trust and application startup

Nginx discards an inbound `Forwarded` value and replaces `X-Forwarded-For`,
`X-Real-IP`, `X-Forwarded-Host`, and `X-Forwarded-Proto` after applying only the
configured real-IP trust boundary. The API must reciprocally trust only its
Nginx peer addresses, never every sender:

```text
python -m uvicorn tc_api.main:app \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips=<exact-nginx-peer-ip-or-cidr> \
  --no-access-log --no-server-header
```

`--no-access-log` is intentional in production: the Nginx `tc_redacted` format
records the normalized `$uri` but omits query strings, bodies, cookies,
authorization, Slack signatures, and timestamps. Structured application logs
must likewise use stable event/resource IDs and sanitized error codes, not raw
provider bodies or secret-bearing headers. Keep development access logs local.

## Slack request controls

The public exact location requires JSON, rejects query strings, permits only
POST, buffers at most 256 KiB within five seconds, rate-limits by the verified
client address, caps concurrent requests, and gives the application three
seconds to acknowledge. It does not set or transform the request body and keeps
the Slack signature/timestamp headers, so FastAPI verifies the received bytes
before JSON decoding. `proxy_next_upstream off` prevents an implicit replay.

If legitimate Slack traffic exceeds a bound, change it only from measured
staging evidence and preserve the exact-path/default-deny contract. Do not add a
public health check, docs route, UI fallback, or general `/api` location.

## Secret and rotation boundary

Slack and provider credentials remain deployment inputs represented by
`SecretStr`; they are not stored in the repository or database. This layout does
not claim that a particular secret store encrypts them. Before production:

1. inject versioned secret references with least-privilege runtime read access;
2. disable/drain the affected receiver or writer, update the complete credential
   bundle atomically, restart, and run credential-free/forged-request checks;
3. perform the provider's real verification in a test workspace, then revoke the
   replaced token/secret and record only version IDs and timestamps;
4. on rollback, restore the prior secret version only while it remains valid and
   close the listener if verification state is ambiguous.

Signing-secret rotation may interrupt HTTP Events because old and new Slack
signatures cannot be assumed to overlap. Bot/app token and signing-secret
rotation are separate operations. The encryption master key signs object-storage
claims; drain the configured claim TTL before changing it or outstanding upload
claims fail closed. Never place a credential in a URL, command history, report,
or log sample.

## Deterministic validation

Run the repository checks plus a real Nginx parser before deployment. One parser
example using the credential-free files is:

```text
docker run --rm --add-host web:127.0.0.1 --add-host api:127.0.0.1 \
  -v "$PWD/deploy/nginx/00-security-common.conf:/etc/nginx/conf.d/00-security-common.conf:ro" \
  -v "$PWD/deploy/nginx/internal.conf:/etc/nginx/conf.d/internal.conf:ro" \
  -v "$PWD/deploy/nginx/slack-http.conf:/etc/nginx/conf.d/slack-http.conf:ro" \
  -v "$PWD/deploy/nginx/internal-allowlist.conf.example:/etc/nginx/tc/internal-allowlist.conf:ro" \
  -v "$PWD/deploy/nginx/trusted-proxies.conf.example:/etc/nginx/tc/trusted-proxies.conf:ro" \
  -v "$PWD/deploy/nginx/internal-security-headers.conf.example:/etc/nginx/tc/internal-security-headers.conf:ro" \
  nginx:1.27-alpine nginx -t
```

Release still requires external black-box requests from both an allowed VPN
source and an Internet source, TLS/DNS inspection, firewall/LB rule review,
deployed log inspection, secret-store/rotation rehearsal, and real Slack request
verification. Keep those results outside credential-bearing artifacts.
