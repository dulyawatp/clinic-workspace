#!/usr/bin/env python3
"""Opt-in, read-only development check for the converted native write lanes.

The isolated synthetic site converted the patient and appointment lanes to
native Frappe/Marley authority; the other families still require the historical
fence and the deferred clinical capabilities stay closed. This reads the DOC
bootstrap, the synthetic directory and the historical ledger, and compares the
exact counts the caller supplies. It writes, retries and repairs nothing, and it
claims nothing about the deployment as a whole: the strict gate is verify.py.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify as strict  # noqa: E402

CONVERTED = tuple(
    name
    for name, (family, _roles) in strict.WRITE_CAPABILITY_GATES.items()
    if family in ("patient", "appointment")
)
SEEDED_PATIENT = ("WS-SIT-1", strict.EXPECTED_PATIENT_NAME)


def lane_problems(capabilities, gates, role="DOC"):
    """Return problems when the converted lanes or the deferral disagree."""

    if not isinstance(capabilities, dict):
        return ["capabilities are not a map"]
    problems = []
    absent = sorted(set(CONVERTED) - set(capabilities))
    if absent:
        problems.append(f"converted capabilities missing: {absent}")
    expected = set(CONVERTED) & strict.expected_enabled_writes(gates, role)
    closed = sorted(set(CONVERTED) - expected)
    if closed:
        problems.append(f"site gates do not open converted lanes: {closed}")
    enabled = {name for name, state in capabilities.items() if state == "enabled"}
    if strict.DEFERRED_WRITE_CAPABILITIES & enabled:
        problems.append(
            f"deferred capabilities enabled: "
            f"{sorted(strict.DEFERRED_WRITE_CAPABILITIES & enabled)}"
        )
    if expected - enabled:
        problems.append(f"converted lanes not enabled: {sorted(expected - enabled)}")
    return problems


def fence_problems(ledger, expected_events, expected_unresolved):
    """Return problems for the exact historical counts, or ``[]``.

    Both counts are caller-supplied; there is no default and no inference from
    the ledger. Matching counts are the only claim made here.
    """

    if not isinstance(ledger, dict):
        return ["historical fence ledger unreadable"]
    problems = []
    if ledger.get("events") != expected_events:
        problems.append(f"fence events {ledger.get('events')} != {expected_events}")
    if ledger.get("unresolved") != expected_unresolved:
        problems.append(
            f"unresolved keys {ledger.get('unresolved')} != {expected_unresolved}"
        )
    return problems


def directory_problems(items, expected=SEEDED_PATIENT):
    """Return problems for the seeded identity; extra synthetic patients are ok."""

    if not isinstance(items, list):
        return ["directory items are not a list"]
    problems = []
    bad = [
        index
        for index, item in enumerate(items)
        if not isinstance(item, dict)
        or set(item) != {"workspace_patient_id", "display_name"}
    ]
    if bad:
        problems.append(f"directory items with an unexpected shape: {bad}")
    matches = [
        item
        for item in items
        if isinstance(item, dict) and item.get("workspace_patient_id") == expected[0]
    ]
    if len(matches) != 1:
        problems.append(f"seeded identity {expected[0]} appears {len(matches)} times")
    elif matches[0].get("display_name") != expected[1]:
        problems.append(f"seeded identity {expected[0]} is not {expected[1]!r}")
    return problems


def count(text):
    """Parse a required non-negative recorded count."""

    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}")
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be non-negative: {text!r}")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only development check for converted native lanes."
    )
    parser.add_argument(
        "--expected-fence-events",
        type=count,
        required=True,
        help="exact recorded fence event count; no default",
    )
    parser.add_argument(
        "--expected-fence-unresolved",
        type=count,
        required=True,
        help="exact recorded unresolved key count; no default",
    )
    args = parser.parse_args(argv)

    credentials = json.loads((strict.STATE_DIR / "credentials.json").read_text())
    if credentials["site"] != strict.SITE or credentials["origin"] != strict.ORIGIN:
        print("refusing: private credentials do not describe this isolated site")
        return 2

    checks = []

    def record(name, ok, detail):
        checks.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")

    gate_probe = strict.in_backend(strict.gate_probe_script())
    gate_values = json.loads(gate_probe.stdout) if gate_probe.returncode == 0 else {}
    gates = {
        family: gate_values.get(key)
        for family, key in strict.GATE_CONFIG_KEYS.items()
    }
    record("site write gates readable", gate_probe.returncode == 0, gates)

    doc = next(u for u in credentials["users"] if u["clinic_code"] == "DOC")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    login = {"usr": doc["email"], "pwd": doc["password"]}
    status, _, body = strict.request(
        opener,
        "/api/method/login",
        method="POST",
        data=urllib.parse.urlencode(login).encode(),
    )
    record(
        "synthetic DOC login",
        status == 200 and b"Logged In" in body,
        {"status": status, "email": doc["email"]},
    )

    status, _, body = strict.request(
        opener, "/api/method/clinic_ui.api.session.bootstrap"
    )
    boot = json.loads(body).get("message", {}) if status == 200 else {}
    capabilities = boot.get("capabilities") or {}
    record(
        "DOC bootstrap is live",
        status == 200
        and boot.get("mode") == "live"
        and boot.get("user", {}).get("roles") == ["DOC"],
        {"status": status, "mode": boot.get("mode"), "roles": boot.get("user", {}).get("roles")},
    )
    problems = lane_problems(capabilities, gates)
    record(
        "converted lanes enabled and deferred capabilities closed",
        not problems,
        {
            "converted": sorted(CONVERTED),
            "deferred": {
                name: capabilities.get(name)
                for name in sorted(strict.DEFERRED_WRITE_CAPABILITIES)
            },
            "problems": problems,
        },
    )

    status, _, body = strict.request(
        opener, "/api/method/clinic_ui.api.directory.synthetic_patients"
    )
    items = (json.loads(body).get("message") or {}).get("items", []) if status == 200 else []
    problems = directory_problems(items) if status == 200 else ["directory request failed"]
    record(
        "seeded synthetic patient still resolves",
        not problems,
        {
            "status": status,
            "observed_count": len(items),
            "workspace_ids": [
                item.get("workspace_patient_id")
                for item in items
                if isinstance(item, dict)
            ],
            "problems": problems,
        },
    )

    probe = strict.in_backend(strict.ledger_probe_script())
    ledger = json.loads(probe.stdout) if probe.returncode == 0 else {}
    problems = fence_problems(
        ledger, args.expected_fence_events, args.expected_fence_unresolved
    )
    record(
        "historical fence counts match the recorded values",
        not problems,
        {
            "events": ledger.get("events"),
            "expected_events": args.expected_fence_events,
            "unresolved": ledger.get("unresolved"),
            "expected_unresolved": args.expected_fence_unresolved,
            "problems": problems,
        },
    )

    print("development check only; the strict deployment gate is verify.py")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
