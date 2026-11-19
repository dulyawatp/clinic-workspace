#!/usr/bin/env python3
"""Opt-in, read-only development check for the converted native write lanes.

The isolated synthetic site converted the patient, appointment, procedure and
purchase-draft lanes to native Frappe/Marley authority; the other families still
require the historical fence and the deferred clinical capabilities stay closed.
The procedure lane is non-consuming - it creates and completes without touching
stock, so ``write.stock.consume`` stays read_only whatever its gate flag says -
and it converted with its site gate still off, so it is checked as disabled
until that flag is opened. The purchase lane converted only its draft:
``write.purchase_order.draft`` follows the purchase gate for the purchasing
roles, while submit and the PO-referenced receipt stay fenced and disabled while
the ledger has unresolved keys. The admin lane converted only ``write.admin.user``
for ADM, following the admin gate; ``write.admin.master`` stays fenced like
submit and is asserted disabled while the ledger has unresolved keys. The
finance lane converted only ``write.invoice.submit`` for CAS: it needs the
caller's own native Sales Invoice create and submit authority, so the synthetic
CAS bootstrap is required and a missing CAS credential fails this run rather
than passing the lane unverified. ``write.payment.receive`` stays fenced and
must remain ``read_only`` while the ledger has unresolved keys. The DOC
bootstrap covers DOC only; the MGR purchase lane is checked from its own
bootstrap when the private state already carries that credential, and the run
reports the gap otherwise. The ADM bootstrap cannot be optional and cannot be
the contract-created ADM user: the enabled admin lane needs the caller's own
native ``User`` create authority, which only the private ``adm_bootstrap``
identity carries, so a missing ``adm_bootstrap`` fails this run rather than
passing the lane unverified. The contract ADM identity is read separately and
must never advertise the admin writes. This reads the DOC, CAS, MGR and ADM
bootstraps, the synthetic directory and the historical ledger, and compares the
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

CONVERTED_FAMILIES = (
    "patient",
    "appointment",
    "procedure",
    "purchase",
    "finance",
    "admin",
)

# Open on this site and expected to stay open.
ALWAYS_OPEN_FAMILIES = ("patient", "appointment")

# Converted writes that must never read ``enabled``, whatever the site gates
# say. The procedure lane completes and creates without consuming stock; stock
# consumption keeps its own review and stays read_only even with the procedure
# flag on. The strict verifier still lists ``write.stock.consume`` under the
# procedure family, so this check overrides that legacy classification rather
# than inheriting it.
NEVER_ENABLED = frozenset({"write.stock.consume"})

# Writes that stay on the historical fence: the purchase lane converted only
# its draft, so an unresolved ledger keeps the submit and the PO-referenced
# receipt disabled, the admin lane converted only its user write, so
# ``write.admin.master`` stays fenced the same way, and the finance lane
# converted only its invoice, so ``write.payment.receive`` remains fenced and
# ``read_only`` while the ledger is unresolved. None of them is ever counted as
# converted here.
FENCED_WRITES = frozenset(
    {
        "write.purchase_order.submit",
        "write.stock.receive",
        "write.payment.receive",
        "write.admin.master",
    }
)

CONVERTED = tuple(
    name
    for name, (family, _roles) in strict.WRITE_CAPABILITY_GATES.items()
    if family in CONVERTED_FAMILIES and name not in FENCED_WRITES
)
SEEDED_PATIENT = ("WS-SIT-1", strict.EXPECTED_PATIENT_NAME)


def _converted(families):
    return {
        name
        for name, (family, _roles) in strict.WRITE_CAPABILITY_GATES.items()
        if family in families
    }


def credential_for(credentials, code):
    """Return the complete synthetic credential for ``code``, or ``None``.

    A missing email or password counts as absent: the check reports that it
    cannot verify the role instead of trying an unusable login.
    """

    users = credentials.get("users", []) if isinstance(credentials, dict) else []
    for user in users:
        if (
            isinstance(user, dict)
            and user.get("clinic_code") == code
            and user.get("email")
            and user.get("password")
        ):
            return user
    return None


def cas_credential_problem(credentials):
    """Return why the CAS bootstrap cannot run, or ``None``.

    The converted invoice lane advertises ``write.invoice.submit`` for CAS only,
    from the caller's own native Sales Invoice create and submit authority, so
    this run needs a complete synthetic CAS credential. A missing credential
    fails this run rather than leaving the converted lane unverified and green.
    """

    if credential_for(credentials, "CAS") is None:
        return (
            "no complete CAS synthetic credential in the private state; "
            "the converted invoice lane cannot be verified"
        )
    return None


def adm_bootstrap_credential(credentials):
    """Return the complete private ``adm_bootstrap`` entry, or ``None``.

    The converted admin lane advertises ``write.admin.user`` only for a caller
    who already holds native ``User`` create authority. That is the bootstrap
    identity (System Manager + Item Manager), never the contract-created ADM
    user, which carries the clinic role alone: its bootstrap cannot demonstrate
    an enabled lane even when the site gate is open.
    """

    entry = credentials.get("adm_bootstrap") if isinstance(credentials, dict) else None
    if (
        isinstance(entry, dict)
        and entry.get("email")
        and entry.get("password")
    ):
        return entry
    return None


def adm_credential_problem(credentials):
    """Return why the ADM bootstrap cannot run, or ``None``.

    The admin lane converted ``write.admin.user`` for ADM only, and the enabled
    state is decided by the caller's own native authority, so this run needs the
    private ``adm_bootstrap`` identity. A contract ADM user is not a substitute:
    a missing bootstrap credential fails this run rather than leaving the lane
    unverified and green.
    """

    if adm_bootstrap_credential(credentials) is None:
        return (
            "no complete adm_bootstrap credential in the private state; "
            "the converted admin lane cannot be verified"
        )
    return None


def expected_converted(gates, role):
    """Return the converted writes that must be enabled for ``role``."""

    return (
        set(CONVERTED) & strict.expected_enabled_writes(gates, role)
    ) - NEVER_ENABLED - FENCED_WRITES


def lane_problems(capabilities, gates, role="DOC"):
    """Return problems when the converted lanes or the deferral disagree.

    The patient and appointment lanes are open here and must stay enabled for
    the roles their policy admits. The procedure lane converted with its site
    gate still off: while the flag is off its capabilities must be published
    and ``read_only``, and while it is on ``write.procedure.create`` and
    ``write.procedure.complete`` must be enabled. That lane completes without
    consuming stock, so ``write.stock.consume`` must stay ``read_only`` even
    with the flag on - the strict verifier's legacy family classification does
    not open it here. The purchase lane converted only
    ``write.purchase_order.draft`` for the purchasing roles: with the purchase
    gate on it must be enabled for them and with the gate off it must stay
    ``read_only``. The admin lane converted only ``write.admin.user`` for ADM:
    with the admin gate on it must be enabled and with the gate off it must stay
    ``read_only``. The finance lane converted only ``write.invoice.submit`` for
    CAS: with the finance gate on it must be enabled and with the gate off it
    must stay ``read_only``; the fenced payment receive is never asserted here.
    Submit, the receipt, payment receive and ``write.admin.master`` are
    fence-gated and are not asserted here; the stock, finance and deferred
    clinical lanes are never asserted ready.
    """

    if not isinstance(capabilities, dict):
        return ["capabilities are not a map"]
    problems = []
    absent = sorted(set(CONVERTED) - set(capabilities))
    if absent:
        problems.append(f"converted capabilities missing: {absent}")
    expected = expected_converted(gates, role)
    admitted = {
        name
        for name in _converted(ALWAYS_OPEN_FAMILIES)
        if role in strict.WRITE_CAPABILITY_GATES[name][1]
    }
    closed = sorted(admitted - expected)
    if closed:
        problems.append(f"site gates do not open converted lanes: {closed}")
    enabled = {name for name, state in capabilities.items() if state == "enabled"}
    if expected - enabled:
        problems.append(f"converted lanes not enabled: {sorted(expected - enabled)}")
    unexpected = sorted((enabled & set(CONVERTED)) - expected - NEVER_ENABLED)
    if unexpected:
        problems.append(f"converted lanes enabled while closed: {unexpected}")
    forbidden = sorted(enabled & NEVER_ENABLED)
    if forbidden:
        problems.append(f"non-consuming procedure lane enabled: {forbidden}")
    if strict.DEFERRED_WRITE_CAPABILITIES & enabled:
        problems.append(
            f"deferred capabilities enabled: "
            f"{sorted(strict.DEFERRED_WRITE_CAPABILITIES & enabled)}"
        )
    return problems


def fenced_write_problems(capabilities, unresolved):
    """Return problems for the fence-gated writes against the ledger, or ``[]``.

    ``unresolved`` is the observed unresolved key count. Only a positive count
    is asserted: the documented state is that an unresolved ledger keeps
    submit, the receipt, payment receive and ``write.admin.master`` disabled. A
    resolved ledger makes no readiness claim either way, and none of these
    writes is ever counted as converted.
    """

    if not isinstance(capabilities, dict):
        return ["capabilities are not a map"]
    if not isinstance(unresolved, int) or isinstance(unresolved, bool):
        return ["historical fence unresolved count unreadable"]
    if unresolved <= 0:
        return []
    problems = []
    unobserved = sorted(name for name in FENCED_WRITES if name not in capabilities)
    if unobserved:
        problems.append(
            f"fence-gated writes not published with {unresolved} unresolved keys: "
            f"{unobserved}"
        )
    enabled = {name for name, state in capabilities.items() if state == "enabled"}
    open_fenced = sorted(enabled & FENCED_WRITES)
    if open_fenced:
        problems.append(
            f"fence-gated writes enabled with {unresolved} unresolved keys: "
            f"{open_fenced}"
        )
    return problems


def contract_admin_problems(capabilities, unresolved):
    """Return problems when the contract ADM identity advertises admin writes.

    The converted user lane needs the caller's own native ``User`` create
    authority, which the contract-created ADM user does not hold. Its bootstrap
    must therefore stay ``read_only`` for ``write.admin.user``, and the fenced
    ``write.admin.master`` keeps the same unresolved-ledger expectation. An
    enabled state there would mean the capability was advertised from the site
    flag rather than from native authority.
    """

    if not isinstance(capabilities, dict):
        return ["capabilities are not a map"]
    problems = []
    if capabilities.get("write.admin.user") == "enabled":
        problems.append(
            "contract ADM identity advertises write.admin.user without native "
            "User create authority"
        )
    problems += fenced_write_problems(capabilities, unresolved)
    return problems


def lane_report(capabilities, gates, role="DOC"):
    """Return each converted lane's expected and observed capability states."""

    expected = expected_converted(gates, role)
    return {
        family: {
            name: {"expected": name in expected, "observed": capabilities.get(name)}
            for name, (cap_family, _roles) in strict.WRITE_CAPABILITY_GATES.items()
            if cap_family == family
        }
        for family in CONVERTED_FAMILIES
    }


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
        "DOC converted lanes and deferred capabilities closed",
        not problems,
        {
            "role": "DOC",
            "converted": sorted(CONVERTED),
            "lanes": lane_report(capabilities, gates),
            "deferred": {
                name: capabilities.get(name)
                for name in sorted(strict.DEFERRED_WRITE_CAPABILITIES)
            },
            "problems": problems,
        },
    )

    # The invoice lane converted for CAS only, from the caller's own native
    # Sales Invoice create and submit authority. There is no substitute
    # bootstrap: a missing CAS credential fails this run rather than leaving
    # the converted lane unverified and green.
    cas = credential_for(credentials, "CAS")
    cas_gap = cas_credential_problem(credentials)
    record(
        "synthetic CAS bootstrap credential present",
        cas_gap is None,
        cas_gap or {"email": cas["email"]},
    )
    cas_capabilities = None
    if cas is not None:
        cas_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        cas_login = {"usr": cas["email"], "pwd": cas["password"]}
        status, _, body = strict.request(
            cas_opener,
            "/api/method/login",
            method="POST",
            data=urllib.parse.urlencode(cas_login).encode(),
        )
        record(
            "synthetic CAS login",
            status == 200 and b"Logged In" in body,
            {"status": status, "email": cas["email"]},
        )
        status, _, body = strict.request(
            cas_opener, "/api/method/clinic_ui.api.session.bootstrap"
        )
        cas_boot = json.loads(body).get("message", {}) if status == 200 else {}
        cas_capabilities = cas_boot.get("capabilities") or {}
        record(
            "CAS bootstrap is live",
            status == 200
            and cas_boot.get("mode") == "live"
            and cas_boot.get("user", {}).get("roles") == ["CAS"],
            {
                "status": status,
                "mode": cas_boot.get("mode"),
                "roles": cas_boot.get("user", {}).get("roles"),
            },
        )

    # The purchase draft lane is advertised for the purchasing roles, which the
    # DOC bootstrap does not cover. An MGR bootstrap is used only when the
    # private state already carries a complete MGR credential; otherwise the
    # run states that it could not verify the role.
    mgr_capabilities = None
    mgr = credential_for(credentials, "MGR")
    if mgr is None:
        print(
            "UNVERIFIED MGR purchase lane: no complete MGR synthetic credential "
            "in the private state; the DOC bootstrap cannot speak for MGR"
        )
    else:
        mgr_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        mgr_login = {"usr": mgr["email"], "pwd": mgr["password"]}
        status, _, body = strict.request(
            mgr_opener,
            "/api/method/login",
            method="POST",
            data=urllib.parse.urlencode(mgr_login).encode(),
        )
        record(
            "synthetic MGR login",
            status == 200 and b"Logged In" in body,
            {"status": status, "email": mgr["email"]},
        )
        status, _, body = strict.request(
            mgr_opener, "/api/method/clinic_ui.api.session.bootstrap"
        )
        mgr_boot = json.loads(body).get("message", {}) if status == 200 else {}
        mgr_capabilities = mgr_boot.get("capabilities") or {}
        record(
            "MGR bootstrap is live",
            status == 200
            and mgr_boot.get("mode") == "live"
            and mgr_boot.get("user", {}).get("roles") == ["MGR"],
            {
                "status": status,
                "mode": mgr_boot.get("mode"),
                "roles": mgr_boot.get("user", {}).get("roles"),
            },
        )

    # The admin lane converted ``write.admin.user`` for ADM only, and its
    # enabled state needs the caller's own native ``User`` create authority.
    # That is the private ``adm_bootstrap`` identity, never the contract ADM
    # user, so the bootstrap credential is required: without it the run fails
    # instead of leaving the converted lane unverified and green.
    adm = adm_bootstrap_credential(credentials)
    adm_gap = adm_credential_problem(credentials)
    record(
        "synthetic ADM bootstrap credential present",
        adm_gap is None,
        adm_gap or {"email": adm["email"], "roles": adm.get("roles")},
    )
    adm_capabilities = None
    if adm is not None:
        adm_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        adm_login = {"usr": adm["email"], "pwd": adm["password"]}
        status, _, body = strict.request(
            adm_opener,
            "/api/method/login",
            method="POST",
            data=urllib.parse.urlencode(adm_login).encode(),
        )
        record(
            "synthetic ADM bootstrap login",
            status == 200 and b"Logged In" in body,
            {"status": status, "email": adm["email"]},
        )
        status, _, body = strict.request(
            adm_opener, "/api/method/clinic_ui.api.session.bootstrap"
        )
        adm_boot = json.loads(body).get("message", {}) if status == 200 else {}
        adm_capabilities = adm_boot.get("capabilities") or {}
        record(
            "ADM bootstrap is live",
            status == 200
            and adm_boot.get("mode") == "live"
            and adm_boot.get("user", {}).get("roles") == ["ADM"],
            {
                "status": status,
                "mode": adm_boot.get("mode"),
                "roles": adm_boot.get("user", {}).get("roles"),
            },
        )

    # The contract-created ADM user is read separately and never stands in for
    # the bootstrap: it must not advertise the admin writes it cannot execute.
    contract_adm = credential_for(credentials, "ADM")
    contract_adm_capabilities = None
    contract_adm_roles = None
    if contract_adm is None:
        print(
            "UNVERIFIED contract ADM identity: no complete ADM synthetic "
            "credential in the private state"
        )
    elif adm is not None and contract_adm["email"] == adm["email"]:
        print(
            "UNVERIFIED contract ADM identity: the ADM synthetic credential is "
            "the adm_bootstrap identity itself"
        )
    else:
        contract_adm_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        contract_adm_login = {
            "usr": contract_adm["email"],
            "pwd": contract_adm["password"],
        }
        status, _, body = strict.request(
            contract_adm_opener,
            "/api/method/login",
            method="POST",
            data=urllib.parse.urlencode(contract_adm_login).encode(),
        )
        record(
            "synthetic contract ADM login",
            status == 200 and b"Logged In" in body,
            {"status": status, "email": contract_adm["email"]},
        )
        status, _, body = strict.request(
            contract_adm_opener, "/api/method/clinic_ui.api.session.bootstrap"
        )
        contract_adm_boot = json.loads(body).get("message", {}) if status == 200 else {}
        contract_adm_capabilities = contract_adm_boot.get("capabilities") or {}
        contract_adm_roles = contract_adm_boot.get("user", {}).get("roles")

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

    if cas_capabilities is not None:
        problems = lane_problems(cas_capabilities, gates, role="CAS")
        problems += fenced_write_problems(cas_capabilities, ledger.get("unresolved"))
        record(
            "CAS invoice lane and fenced payment receive",
            not problems,
            {
                "role": "CAS",
                "lanes": lane_report(cas_capabilities, gates, role="CAS"),
                "fenced": {
                    name: cas_capabilities.get(name)
                    for name in sorted(FENCED_WRITES)
                },
                "problems": problems,
            },
        )

    if mgr_capabilities is not None:
        problems = lane_problems(mgr_capabilities, gates, role="MGR")
        problems += fenced_write_problems(mgr_capabilities, ledger.get("unresolved"))
        record(
            "MGR purchase draft lane and fenced writes",
            not problems,
            {
                "role": "MGR",
                "lanes": lane_report(mgr_capabilities, gates, role="MGR"),
                "fenced": {
                    name: mgr_capabilities.get(name)
                    for name in sorted(FENCED_WRITES)
                },
                "problems": problems,
            },
        )

    if adm_capabilities is not None:
        problems = lane_problems(adm_capabilities, gates, role="ADM")
        problems += fenced_write_problems(adm_capabilities, ledger.get("unresolved"))
        record(
            "ADM admin user lane and fenced admin master",
            not problems,
            {
                "role": "ADM",
                "identity": "adm_bootstrap",
                "lanes": lane_report(adm_capabilities, gates, role="ADM"),
                "fenced": {
                    name: adm_capabilities.get(name)
                    for name in sorted(FENCED_WRITES)
                },
                "problems": problems,
            },
        )

    if contract_adm_capabilities is not None:
        problems = contract_admin_problems(
            contract_adm_capabilities, ledger.get("unresolved")
        )
        record(
            "contract ADM identity does not advertise the admin writes",
            contract_adm_roles == ["ADM"] and not problems,
            {
                "role": contract_adm_roles,
                "admin_user": contract_adm_capabilities.get("write.admin.user"),
                "admin_master": contract_adm_capabilities.get("write.admin.master"),
                "problems": problems,
            },
        )

    print("development check only; the strict deployment gate is verify.py")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
