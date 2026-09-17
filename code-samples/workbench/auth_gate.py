"""The workbench's login gate: the same login as the firm's portal (Supabase).

It mirrors the authentication the portal's other services already use. Two ways to authenticate
in the same house would be worse than one imperfect way, so the logic is the same:

1. `GET /auth/v1/user` with the token. 200 means valid. **There is no JWT secret**: Supabase
   itself decides whether the token is any good.
2. Team membership is a lookup in `portal_users`, `department_users` or `team_profiles`, made
   **with the user's own bearer token**, not the bare anon key. The row-level policies allow
   `authenticated`; with `anon` the query comes back empty and everyone gets a 403.
3. A short per-token cache, so Supabase isn't called on every request.

If Supabase is down, nobody gets in (503). A network error in the middle of the team check also
refuses. It fails closed, like the rest of the system (see ADR 7).

## Three deliberate differences from the portal's implementation

- **The cache key is the token's SHA-256, not the token.** Keeping live bearer tokens in a
  dictionary of a long-running process is needless exposure: a memory dump or a debug log would
  hand over a valid credential. The hash works just as well as a key.
- **The cache has a size cap and entries really expire.** The portal's version never evicts
  anything. Supabase tokens rotate every hour, so over a few weeks the dictionary only grows.
- **`removeprefix` instead of `replace`.** `.replace("Bearer ", "")` removes that text from any
  position, not just from the start.

## What this module does NOT do

It doesn't decide WHICH companies a person can see. Whoever gets in sees all of them. That is
the firm's stated rule: any employee may look at a colleague's clients. If that ever changes,
this is the place to change it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time

import httpx

log = logging.getLogger("workbench")

SUPABASE = "https://exemplo.supabase.co"
CACHE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 500
TIMEOUT_SECONDS = 8.0

ADMIN_EMAILS = {
    "ana@escritorio.example",
    "bruno@escritorio.example",
    "carla@escritorio.example",
}


class NotAuthenticated(Exception):
    """Token missing, invalid or expired."""


class NotTeamMember(Exception):
    """The token is valid, but whoever presented it is not on the team."""


class AuthUnavailable(Exception):
    """Supabase didn't answer. Nobody gets in until it's back."""


class AuthGate:
    """Holds the configuration and the cache. One instance per app, like the write lock."""

    def __init__(self, url: str | None = None, anon: str | None = None) -> None:
        self.url = (url or os.environ.get("SUPABASE_URL") or "").rstrip("/")
        self.anon = anon or os.environ.get("SUPABASE_ANON") or ""
        self._cache: dict[str, tuple[float, dict]] = {}

    @property
    def enabled(self) -> bool:
        """Without both variables, the workbench runs as it always did: locally."""
        return bool(self.url and self.anon)

    def _store(self, lookup_key: str, person: dict) -> None:
        now = time.time()
        # Drop expired entries before checking the size; otherwise dead entries would be the
        # ones filling up the cap.
        self._cache = {c: v for c, v in self._cache.items() if v[0] > now}
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            oldest = min(self._cache, key=lambda c: self._cache[c][0])
            del self._cache[oldest]
        self._cache[lookup_key] = (now + CACHE_TTL_SECONDS, person)

    async def identify(self, header: str) -> dict:
        """`Authorization` header -> `{id, email, admin}`, or an exception."""
        token = (header or "").strip()
        if token.startswith("Bearer "):
            token = token.removeprefix("Bearer ").strip()
        if not token:
            raise NotAuthenticated("no token")

        lookup_key = hashlib.sha256(token.encode()).hexdigest()
        if (cached := self._cache.get(lookup_key)) and cached[0] > time.time():
            return cached[1]

        auth_headers = {"apikey": self.anon, "Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            try:
                response = await client.get(
                    f"{self.url}/auth/v1/user", headers=auth_headers
                )
            except httpx.HTTPError as err:
                log.warning("Supabase didn't answer during authentication: %s", err)
                raise AuthUnavailable(str(err)) from err
            if response.status_code != 200:
                raise NotAuthenticated("invalid or expired token")

            data = response.json()
            email = (data.get("email") or "").lower()
            identifier = data.get("id") or ""
            if not await self._is_team_member(client, email, identifier, auth_headers):
                raise NotTeamMember(email or identifier)

        person = {
            "id": identifier,
            "email": email,
            "admin": email in ADMIN_EMAILS,
        }
        self._store(lookup_key, person)
        return person

    async def _is_team_member(
        self, client: httpx.AsyncClient, email: str, identifier: str, auth_headers: dict
    ) -> bool:
        if email in ADMIN_EMAILS:
            return True
        # Three tables, three ways of being on the team. Any one of them is enough.
        queries = []
        if email:
            queries += [
                ("portal_users", {"email": f"eq.{email}", "select": "active", "limit": "1"}),
                (
                    "department_users",
                    {"email": f"eq.{email}", "active": "eq.true", "select": "id", "limit": "1"},
                ),
            ]
        if identifier:
            queries.append(
                ("team_profiles", {"id": f"eq.{identifier}", "select": "active", "limit": "1"})
            )
        for table, query_params in queries:
            try:
                response = await client.get(
                    f"{self.url}/rest/v1/{table}", params=query_params, headers=auth_headers
                )
            except httpx.HTTPError as err:
                # Refuse, don't allow: a flaky network must not turn into an open door.
                log.warning("query to %s failed: %s", table, err)
                return False
            if response.status_code != 200:
                continue
            lines = response.json()
            if lines and lines[0].get("active") is not False:
                return True
        return False
