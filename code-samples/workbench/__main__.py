"""Excerpt of the workbench entry point (`python -m workbench`) showing only `_is_loopback` and
the refusal to listen off loopback without login; logging setup, the uvicorn import check,
opening the browser and starting the server are left out.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .schema import DEFAULT_PATH

log = logging.getLogger("workbench")


def _is_loopback(host: str) -> bool:
    """Only loopback stays private. `0.0.0.0` listens on EVERY interface."""
    return host in {"127.0.0.1", "::1", "localhost", "[::1]"} or host.startswith("127.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="workbench",
        description=(
            "Starts the reconciliation workbench in the browser (local, no external network)."
        ),
    )
    parser.add_argument("--listen-port", type=int, default=8000)
    parser.add_argument(
        "--host",
        default=os.environ.get("WORKBENCH_HOST", "127.0.0.1"),
        help=(
            "listen address. The default is 127.0.0.1: bank statements should not be served "
            "on the network. Exposing it requires the portal login to be configured."
        ),
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_PATH, help="SQLite file")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    ...  # … logging setup, and exit with 2 if uvicorn is not installed

    from . import auth_gate

    ...  # … `from .app import create_app`

    if not _is_loopback(args.host) and not auth_gate.AuthGate().enabled:
        if not os.environ.get("WORKBENCH_NO_AUTH_GATE"):
            # Refuse BEFORE opening the port. The other two guards don't cover this case: the
            # `ALLOWED_ORIGINS` one depends on someone filling in that variable, and the
            # `X-Forwarded-*` header check only sees traffic that went through the proxy. When
            # listening on the LAN, any machine on the network reaches IP:port directly, with no
            # proxy and no headers at all.
            print(
                f"the workbench refuses to listen on {args.host} without the portal login.\n"
                "\n"
                "Listening outside 127.0.0.1 means any machine on the network can\n"
                "reach the bank statements of every company. Configure:\n"
                "\n"
                "  SUPABASE_URL=https://exemplo.supabase.co\n"
                "  SUPABASE_ANON=<the public anon key>\n"
                "\n"
                "in /etc/workbench/environment, and restart the service.\n"
                "To expose it without login anyway: WORKBENCH_NO_AUTH_GATE=1.",
                file=sys.stderr,
            )
            return 2
        log.warning(
            "listening on %s WITHOUT login, because WORKBENCH_NO_AUTH_GATE is set. Don't run "
            "it like this with client data.",
            args.host,
        )

    ...  # … print the address, open the browser, uvicorn.run(create_app(args.database), ...)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
