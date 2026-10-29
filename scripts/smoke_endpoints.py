"""Hit every reachable GET endpoint and report what breaks.

Built for the two moments either side of a database reset: run it before to
capture a baseline, run it after to prove the reset changed only the data and
not the behaviour. The interesting output is not "200" — it is the diff
between the two runs.

What counts as a failure:

    5xx            always a bug. The server broke, regardless of the data.
    404 on a list  suspicious after a wipe, fine before — a list endpoint
                   should answer with an empty array, not Not Found.
    401 / 403      not a failure. Without a token that is the correct answer,
                   and it proves the route exists and its guard runs.

Only GET is exercised. Nothing here writes, so it is safe against production
at any time, including before a reset you have not yet backed up.

Usage:
    BASE_URL=https://appbe.odos.market/api \\
    ADMIN_TOKEN=... VENDOR_TOKEN=... CUSTOMER_TOKEN=... \\
    .venv/bin/python scripts/smoke_endpoints.py [--json out.json]

Any token may be omitted; its routes are then reported as skipped rather than
silently passing on a 401.
"""

from __future__ import annotations

import json
import os
import re
import sys

# Run from anywhere: `python scripts/x.py` puts scripts/ on sys.path, not the
# project root, so `import app` fails with ModuleNotFoundError. Adding the
# parent directory here means the script works without the caller having to
# remember PYTHONPATH.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from dataclasses import asdict, dataclass

import httpx

from app.main import app

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000/api").rstrip("/")
TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT", "20"))

TOKENS = {
    "admin": os.environ.get("ADMIN_TOKEN", ""),
    "vendor": os.environ.get("VENDOR_TOKEN", ""),
    "customer": os.environ.get("CUSTOMER_TOKEN", ""),
}

PATH_PARAM = re.compile(r"\{([^}]+)\}")


def audience_for(path: str) -> str:
    if "/admin" in path:
        return "admin"
    if "/vendor" in path or "/courier" in path:
        return "vendor"
    return "customer"


@dataclass
class Result:
    path: str
    audience: str
    status: int | None
    ms: int
    note: str = ""

    @property
    def broken(self) -> bool:
        return self.status is not None and self.status >= 500

    @property
    def skipped(self) -> bool:
        return self.status is None


def collect_paths() -> list[tuple[str, str]]:
    """Every GET route that needs no path parameter.

    Routes with `{id}` are deliberately left out rather than fed a guessed
    value: a 404 from a made-up id would be indistinguishable from a 404
    caused by a broken route, which is the thing this is meant to detect.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", "")
        if not methods or "GET" not in methods or not path.startswith("/api"):
            continue
        if PATH_PARAM.search(path) or path in seen:
            continue
        seen.add(path)
        out.append((path[len("/api"):] or "/", audience_for(path)))
    return sorted(out)


def run() -> list[Result]:
    results: list[Result] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for path, audience in collect_paths():
            token = TOKENS.get(audience, "")
            if audience in ("admin", "vendor") and not token:
                results.append(Result(path, audience, None, 0, f"no {audience} token"))
                continue

            headers = {"Authorization": f"Bearer {token}"} if token else {}
            started = time.perf_counter()
            try:
                response = client.get(f"{BASE_URL}{path}", headers=headers)
                elapsed = int((time.perf_counter() - started) * 1000)
                results.append(Result(path, audience, response.status_code, elapsed))
            except Exception as exc:  # network, TLS, timeout
                elapsed = int((time.perf_counter() - started) * 1000)
                results.append(
                    Result(path, audience, 0, elapsed, type(exc).__name__)
                )
    return results


def main() -> int:
    results = run()
    broken = [r for r in results if r.broken or r.status == 0]
    skipped = [r for r in results if r.skipped]
    ok = [r for r in results if not r.broken and not r.skipped and r.status != 0]

    print(f"\nBASE_URL  {BASE_URL}")
    print(f"checked   {len(results)}   ok {len(ok)}   broken {len(broken)}   skipped {len(skipped)}\n")

    if broken:
        print("BROKEN")
        for r in sorted(broken, key=lambda r: r.path):
            print(f"  {r.status or 'ERR':>3}  {r.path}  {r.note}")
        print()

    if skipped:
        print(f"SKIPPED ({len(skipped)}) — supply tokens to cover these")
        for audience in ("admin", "vendor"):
            paths = [r.path for r in skipped if r.audience == audience]
            if paths:
                print(f"  {audience}: {len(paths)} routes")
        print()

    # Status spread, so an unexpected cliff of 404s after a wipe is visible.
    spread: dict[int, int] = {}
    for r in ok:
        spread[r.status] = spread.get(r.status, 0) + 1
    print("STATUS SPREAD")
    for status in sorted(spread):
        print(f"  {status}: {spread[status]}")

    if "--json" in sys.argv:
        target = sys.argv[sys.argv.index("--json") + 1]
        with open(target, "w") as handle:
            json.dump([asdict(r) for r in results], handle, indent=2)
        print(f"\nwrote {target}")

    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
