# 7. Secure by default for a small team

**Status:** accepted · in production

## Context

The workbench started as a single-user local tool. It is now served to the firm's team behind a
reverse proxy, and it holds every client's bank statements. The firm already has a portal with
Supabase login, and a second authentication scheme in the same house would be worse than one
imperfect scheme.

## Decision

**Authentication is a middleware keyed on the path, not a per-route dependency.**
- Every `/api/` route requires the portal token, except a version probe that carries no data.
- There are dozens of routes. With a per-route dependency, the next route could be born open by
  mistake. With the path rule, it is born protected.

**It fails closed.**
- The token is validated by Supabase itself, so there is no JWT secret on the server.
- If the identity provider is down, or the team-membership lookup errors, nobody gets in (`503`).
- Membership is queried **with the user's own bearer token**. With the anonymous key, row-level
  policies return empty results and everyone would be locked out.

**Credentials are handled carefully.**
- The token cache is keyed by the token's SHA-256, never by the token itself.
- The cache has a size cap and a real expiry.
- `removeprefix("Bearer ")` is used, not `replace`, which would remove the text anywhere in the
  header.

**It refuses to start exposed.** By default the server listens on `127.0.0.1`. On any other
address, it **refuses to start** unless login is configured. Two other guards (a CORS allow-list
and a check for `X-Forwarded-*` headers) can't cover a LAN machine reaching `ip:port` directly.
Exposing the server without login requires an explicit, deliberately named flag.

**Everything leaves a trail.** Every request that changes data writes an audit record: who did it,
what changed, and refused attempts too.

## Consequences

- One login for the whole portal.
- A misconfiguration fails loudly at startup instead of silently publishing every client's bank
  data on the network.

## Code

- [`workbench/auth_gate.py`](../../code-samples/workbench/auth_gate.py)
- [`workbench/app.py`](../../code-samples/workbench/app.py)
- [`workbench/__main__.py`](../../code-samples/workbench/__main__.py)
