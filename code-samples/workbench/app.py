"""Excerpt of the workbench API: the login middleware, the guard that refuses proxied requests
while login is off, and two of the 33 routes — `POST /api/companies/{id}/statements`, the import
that answers 202 with a queue ticket instead of blocking, and
`POST /api/entries/{id}/category`, the reclassification that carries its own scope ("all similar"
or "only this one") and says which periods already went to the accounting system. The audit-trail
middleware, CORS, the write lock, the other 31 routes and the static UI are left out.

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

import json
import logging
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from . import auth_gate, exporting, importing
from . import queue as import_queue
from . import repository as repo
from .schema import DEFAULT_PATH, open_db

log = logging.getLogger("workbench")


def _who(request: Request) -> str | None:
    """E-mail of whoever is asking, or `None` with the login gate off."""
    return (getattr(request.state, "person", None) or {}).get("email")


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

    ...  # … the audit-trail middleware and CORS, which goes in last so it wraps the rest

    # One queue per app, not a global one, so two `create_app` in a test never fight each other.
    queue = import_queue.ImportQueue()
    cx = database if isinstance(database, sqlite3.Connection) else open_db(database)

    ...  # … `/api/version`, the audit-trail route, companies and the chart of accounts

    # ------------------------------------------------------------------------------- importing

    @app.post("/api/companies/{company_id}/statements")
    async def run_import(
        request: Request,
        company_id: int,
        files: list[UploadFile] = File(...),
        opening_balance: str | None = Form(default=None),
        corrections: str | None = Form(default=None),
    ):
        """`corrections` is the JSON the UI sends back after asking.

        `{"p37-b2979": {"ocr_text": "238,74D", "amount": "-238,34"}}` — the address of the line,
        what OCR had read, and what the person read off the paper. The amount goes in through the
        same gate as always: typed wrong, the statement doesn't close.
        """
        supplied = {}
        if corrections:
            try:
                supplied = json.loads(corrections)
            except ValueError:
                raise HTTPException(400, "corrections is not valid JSON")
            if not isinstance(supplied, dict):
                raise HTTPException(400, "corrections has to be an object")
        # Read the files HERE: the `UploadFile` does not survive the end of the
        # request, and whoever reads them is a worker on another thread, later.
        uploaded = [
            (file.filename or "unnamed.pdf", await file.read())
            for file in files
        ]
        who = _who(request)

        def read_all() -> list:
            return [
                importing.import_file(
                    cx, company_id, label, content,
                    opening_balance=opening_balance,
                    who=who,
                    # Corrections belong to ONE file: the addresses are positions
                    # inside it. Sending several files with corrections attached
                    # would mix addresses from different documents, and the
                    # `ocr_text` guard would refuse in silence — better to apply
                    # them only when there is a single file.
                    corrections=supplied if len(uploaded) == 1 else None,
                ).as_dict()
                for label, content in uploaded
            ]

        job = queue.accept(", ".join(label for label, _ in uploaded), read_all)
        # 202: accepted, not done yet. The UI follows it by the ticket.
        return JSONResponse(job.as_dict(position=0), status_code=202)

    ...  # … following a ticket, the dashboard, the balance proof and the entry list

    @app.post("/api/entries/{entry_id}/category")
    def reclassify(
        entry_id: int,
        category_id: int = Form(...),
        ledger_account: str | None = Form(None),
        scope: str = Form("one"),
    ):
        """Corrects the category of one entry, or of every similar one.

        `scope="similar"` reaches **other periods**, including those already exported. That is
        wanted — fixing only the open month would leave the history inconsistent — but the
        response carries `already_exported` so the UI can warn: the batch is a one-way street,
        and the same correction has to be made in the accounting system.

        ## Changing ONLY this one is a decision about the document, not a rule

        The UI asks "change all of them, next months included" or "change only this one". Whoever
        picks **only this one** is saying that entry is the exception — a `PGTOS-CARTAO DEBITO`
        that in that month was Simples Nacional, say.

        Without marking it, that change counted as a dissenting vote in the suggestion's tally:
        out of 71 entries of the same case, correcting ONE dropped confidence to 0.99 and sent
        the other 70 back to the manual queue every month. The exception contaminated the rule.

        Marked as `one_off`, it stays out of the tally. The group keeps confidence 1.0 and goes
        on classifying itself.

        This only applies when there is something to change: the first classification of a
        pending entry is an ordinary decision and **does** feed the learning, even through this
        route. `scope="similar"` clears the mark on everything it reaches, because at that point
        the decision has become the rule.
        """
        company_id = company_id_of(cx, entry_id)
        model_entry = repo.one_entry(cx, entry_id)
        targets = [model_entry]
        if scope == "similar":
            targets += repo.similar(cx, company_id, model_entry)

        category_changed = model_entry["category_id"] not in (None, category_id)
        one_off = scope != "similar" and category_changed
        for target in targets:
            repo.classify(
                cx, target["id"], category_id, ledger_account, target["edited_description"], one_off
            )
        return {
            "ok": True,
            "affected": len(targets),
            "already_exported": _exported_periods(
                cx, company_id, {a["period"] for a in targets}
            ),
        }

    ...  # … editing a description, transfers, config, batch generation and the static UI
    return app


...  # … the other module-level helpers: the code fingerprint, the asset version stamp, `_json`


def _exported_periods(cx, company_id: int, periods: set[str]) -> list[str]:
    """Which of those periods have already gone to the accounting system.

    The batch is a one-way street: what got in there does not come back. Reclassifying is
    legitimate — the accountant asked for it — but he needs to know that the same correction has
    to be made at the destination, otherwise the two systems end up saying different things about
    the same entry.
    """
    from export.record import read_file

    data = repo.company(cx, company_id)
    if data is None:
        return []
    exported = {
        incoming.period
        for incoming in read_file(exporting.EXPORT_RECORD)
        if incoming.cnpj == data.head_office_cnpj
    }
    return sorted(periods & exported)


def company_id_of(cx, entry_id: int) -> int:
    return cx.execute(
        "SELECT company_id FROM entry WHERE id = ?", (entry_id,)
    ).fetchone()["company_id"]
