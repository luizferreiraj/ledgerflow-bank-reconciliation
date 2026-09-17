"""Excerpt of the workbench API showing only the login middleware and the guard that refuses
proxied requests while login is off, inside `create_app`; the routes, the audit-trail middleware,
CORS and the rest of the app setup are left out.

The service listens on `127.0.0.1`: bank statements are confidential and should not leave the
machine. To serve the team, an HTTPS reverse proxy in front publishes it; the service itself stays
on localhost.

`ALLOWED_ORIGINS` opens CORS for a UI served from another domain. Empty (the default) means the
CORS middleware is not installed: only the UI served from here can talk to the API, which is the
usual behaviour.

Every route returns JSON; the UI is static HTML served from here too. A data error never becomes
an HTTP 500: it becomes a field in the response body, the same discipline as the statement parser
and the export batch.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import auth_gate
from .schema import DEFAULT_PATH

log = logging.getLogger("workbench")


def create_app(database: Path | sqlite3.Connection = DEFAULT_PATH) -> FastAPI:
    """`database` accepts the file path or an already open connection.

    The open connection exists for tests: opening a second connection to the same file makes
    SQLite refuse writes with `database is locked` when both write at the same time. In
    production, `__main__` passes the path.
    """
    app = FastAPI(title="Reconciliation Workbench", docs_url=None, redoc_url=None)
    gate = auth_gate.AuthGate()
    if gate.enabled:

        @app.middleware("http")
        async def require_login(request, call_next):
            """Every `/api/` route requires the portal token, except `/api/version`.

            A middleware rather than a per-route `Depends`, on purpose: there are 33 routes
            today, and the 34th must not be born open because someone forgot. Here the rule is
            the path, so a new route is born protected.

            `/api/version` is exempt because it is a liveness probe: it carries nobody's data,
            and it lets anyone check from outside whether the service is up and whether the
            login gate is on.
            """
            pathname = request.url.path
            if not pathname.startswith("/api/") or pathname == "/api/version":
                return await call_next(request)
            try:
                request.state.person = await gate.identify(
                    request.headers.get("authorization", "")
                )
            except auth_gate.NotAuthenticated:
                return JSONResponse({"error": "not authenticated"}, status_code=401)
            except auth_gate.NotTeamMember:
                return JSONResponse(
                    {"error": "this login is not a member of the firm's team"}, status_code=403
                )
            except auth_gate.AuthUnavailable:
                # Don't let the request through: if we can't confirm who it is, nobody gets in.
                return JSONResponse(
                    {"error": "could not confirm the login right now"}, status_code=503
                )
            return await call_next(request)

        log.info("login gate on: portal login required on /api/")
    elif os.environ.get("ALLOWED_ORIGINS", "").strip() and not os.environ.get(
        "WORKBENCH_NO_AUTH_GATE"
    ):
        # Refuse to start instead of logging a warning. `ALLOWED_ORIGINS` being set means the UI
        # comes from another address, i.e. a proxy is publishing this. In that combination,
        # "login gate off" means every client's bank statements are open on the network, and a
        # warning in a log wouldn't be read in time.
        #
        # Same discipline as a file that doesn't close at 0.00: stopping early is cheap, finding
        # out later is not. Whoever really wants both declares `WORKBENCH_NO_AUTH_GATE=1` and
        # owns the decision.
        raise RuntimeError(
            "ALLOWED_ORIGINS is set, but the login gate is off: the workbench "
            "would be published without login. Set SUPABASE_URL and SUPABASE_ANON. "
            "To publish without login anyway, set WORKBENCH_NO_AUTH_GATE=1."
        )

    if not gate.enabled and not os.environ.get("WORKBENCH_NO_AUTH_GATE"):

        @app.middleware("http")
        async def refuse_if_published(request, call_next):
            """With no login gate, serve only clients that connect directly, never via a proxy.

            The startup guard looks at `ALLOWED_ORIGINS`, and that has a hole: the backend
            serves its own UI, so in an install reached by IP address the origin is the same
            and CORS isn't needed. `ALLOWED_ORIGINS` stays empty, the guard never fires, and the
            workbench ends up published on the network without login.

            `X-Forwarded-*` headers only appear when someone is publishing the service. If they
            show up while the login gate is off, the answer is a refusal, and since the check
            runs on every request there is nothing to forget to configure.

            Locally, without a proxy, none of these headers exist and nothing changes.
            """
            came_through_proxy = any(
                c in request.headers
                for c in ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host")
            )
            if came_through_proxy:
                log.warning(
                    "refused: request came through a proxy (%s) and the login gate is off",
                    request.client.host if request.client else "?",
                )
                return JSONResponse(
                    {
                        "error": (
                            "This workbench is being published through a proxy, but without "
                            "login. Set SUPABASE_URL and SUPABASE_ANON in "
                            "/etc/workbench/environment and restart the service."
                        )
                    },
                    status_code=503,
                )
            return await call_next(request)

    ...  # … audit-trail middleware, CORS, write lock, import queue, routes and static UI
    return app
