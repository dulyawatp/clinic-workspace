#!/usr/bin/env python3
"""Refuse to deploy the isolated stack onto resources this project does not own.

Forces the Compose project name (`-p marley-sit`) because an inherited
`COMPOSE_PROJECT_NAME` must never redirect this deployment. Then checks that:

  * the resolved Compose model name is the forced project name;
  * every existing container and network labelled with that project was created
    from this exact compose file;
  * the four expected volumes either do not exist or are labelled as this
    project's volumes (an unlabelled or foreign volume with a colliding name is
    refused);
  * the published loopback port, if already bound, belongs to this project;
  * no other Compose project claims this project name.

Read-only: it never starts, stops, deletes or recreates anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = "marley-sit"
REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = (REPO_ROOT / "deploy/local-sit/compose.yaml").resolve()
EXPECTED_VOLUMES = {
    "marley-sit-sites",
    "marley-sit-logs",
    "marley-sit-db-data",
    "marley-sit-redis-queue-data",
}
EXPECTED_NETWORK = f"{PROJECT}_default"
PUBLISHED_PORT = "18081"
SITE = "marley-sit.local"

# Family-master inventory the SIT lanes seed through the admin contract. Design
# Amendment A1 (budget v3) added the two prerequisites the install lacks: ``uom``
# and ``supplier_group``. Amendment A1.1 added ``practitioner`` (one Healthcare
# Practitioner for the SIT doctor user); it is order-independent relative to the
# admin chain.
MASTER_DOCTYPES = {
    "supplier_group": "Supplier Group",
    "uom": "UOM",
    "warehouse": "Warehouse",
    "supplier": "Supplier",
    "customer": "Customer",
    "item": "Item",
    "price_list": "Price List",
    "mode_of_payment": "Mode of Payment",
    "procedure_template": "Clinical Procedure Template",
    "practitioner": "Healthcare Practitioner",
}

# The SIT A user the practitioner row links (Amendment A1.1). The real user is
# created in the 11B window, so absence is reported, never a refusal.
PRACTITIONER_LINK_USER = "sit.doc@example.invalid"

# DocTypes the admin contract refuses pre-fence when the acting user has no
# native create authority (`native_admin_can_create` / `native_admin_can_write`).
# `item_price` is the bundled child of the item op and `user` is the admin-user
# op's target; both are probed because both can deny the whole surface.
ADMIN_AUTHORITY_DOCTYPES = {
    "uom": "UOM",
    "supplier_group": "Supplier Group",
    "price_list": "Price List",
    "item": "Item",
    "item_price": "Item Price",
    "mode_of_payment": "Mode of Payment",
    "procedure_template": "Clinical Procedure Template",
    "warehouse": "Warehouse",
    "supplier": "Supplier",
    "practitioner": "Healthcare Practitioner",
    "user": "User",
}

failures: list[str] = []


def admin_install_probe_script() -> str:
    """Backend probe: the contract's own admin install preflight, read-only.

    This calls the adapter the gate actually uses, so a field-map drift is caught
    here instead of by a browser discovering 403s after the window opens
    (review round 2).
    """

    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json\n"
        "import frappe\n"
        f"frappe.init(site={SITE!r}, sites_path='.')\n"
        "frappe.connect()\n"
        "from clinic_ui.runtime.frappe_gateway import Gateway\n"
        "print(json.dumps({\n"
        "    'problem': Gateway().native_admin_install_problem(),\n"
        "}))\n"
        "PY"
    )


def admin_authority_probe_script(email: str) -> str:
    """Backend probe: one user's native create authority for the admin surface.

    Read-only. The check is the same one the endpoint makes before the fence
    (`frappe.has_permission(<DocType>, 'create', user=<actor>)`), evaluated with
    an explicit ``user`` argument so no session is switched and no row is
    written. A failure here is the refusal an operator would otherwise discover
    as a 403 in the browser after the window opened.
    """

    doctypes = repr(dict(ADMIN_AUTHORITY_DOCTYPES))
    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json\n"
        "import frappe\n"
        f"frappe.init(site={SITE!r}, sites_path='.')\n"
        "frappe.connect()\n"
        f"email = {email!r}\n"
        f"doctypes = {doctypes}\n"
        "exists = bool(frappe.db.exists('User', email))\n"
        "authority = {}\n"
        "for label, doctype in doctypes.items():\n"
        "    try:\n"
        "        authority[label] = bool(\n"
        "            frappe.has_permission(doctype, ptype='create', user=email)\n"
        "        )\n"
        "    except Exception:\n"
        "        authority[label] = None\n"
        "print(json.dumps({\n"
        "    'email': email,\n"
        "    'exists': exists,\n"
        "    'authority': authority,\n"
        "}))\n"
        "PY"
    )


def admin_authority_problems(report):
    """Return the named native-authority problems for one report."""

    if not isinstance(report, dict):
        return ["admin authority probe unreadable"]
    email = report.get("email")
    if not isinstance(email, str) or not email.strip():
        return ["admin authority probe reported no account"]
    if report.get("exists") is not True:
        return [f"account {email} does not exist"]
    authority = report.get("authority")
    if not isinstance(authority, dict):
        return ["admin authority map unreadable"]
    problems = []
    for label in ADMIN_AUTHORITY_DOCTYPES:
        if label not in authority:
            problems.append(f"no authority reading for {label}")
            continue
        value = authority[label]
        if value is None:
            problems.append(f"authority unreadable for {label}")
        elif value is not True:
            problems.append(f"missing create authority for {label}")
    return problems


def admin_install_problems(report):
    """Return the named problems for one admin install-preflight report."""

    if not isinstance(report, dict):
        return ["admin install probe unreadable"]
    if "problem" not in report:
        return ["admin install probe reported no problem field"]
    problem = report["problem"]
    if problem is None:
        return []
    if not isinstance(problem, str) or not problem.strip():
        return ["admin install probe returned an unreadable problem"]
    return [f"admin install preflight: {problem}"]


def masters_order_problems(inventory):
    """Return named dependency-order problems for the master inventory.

    The approved seed order (Amendment A1) is uom -> supplier_group -> price list
    -> item (which writes an Item Price against the list) -> procedure template
    (which names the item). A supplier row cannot precede any Supplier Group, and
    an item row needs an installed UOM. Warehouse, supplier and customer rows are
    reported present-or-absent, not asserted: the SIT lanes decide when to seed
    them.
    """

    if not isinstance(inventory, dict):
        return ["master inventory unreadable"]
    problems = []
    counts = {}
    for kind in MASTER_DOCTYPES:
        value = inventory.get(kind)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return [f"master count unreadable for {kind}"]
        counts[kind] = value
    if counts["item"] > 0 and counts["uom"] == 0:
        problems.append("items exist without any UOM")
    if counts["item"] > 0 and counts["price_list"] == 0:
        problems.append("items exist without any Price List")
    if counts["supplier"] > 0 and counts["supplier_group"] == 0:
        problems.append("suppliers exist without any Supplier Group")
    if counts["procedure_template"] > 0 and counts["item"] == 0:
        problems.append("procedure templates exist without any Item")
    return problems


def masters_prerequisite_problems(company, inventory, practitioner=None):
    """Return the named D2 data-prerequisite problems for the master lanes.

    The admin contract refuses every master op pre-fence when no company
    resolves, so the preflight refuses it here instead of letting a lane
    discover it. UOM and Supplier Group are contract masters now: their absence
    is the next seed step, reported, never a refusal.

    Amendment A1.1 adds the practitioner kind: its mandatory Link targets are
    read from the installed meta (probe) and any zero-row target is a refusal,
    because the contract would refuse the op. The linked SIT user is reported
    separately — it is created in the 11B window — and never refused here.
    """

    problems = []
    if not isinstance(company, str) or not company.strip():
        problems.append(
            "no default Company resolves for the admin master surface"
        )
    if not isinstance(inventory, dict):
        problems.append("master inventory unreadable")
    if practitioner is not None:
        problems.extend(practitioner_prerequisite_problems(practitioner))
    return problems


def practitioner_prerequisite_problems(practitioner):
    """Return the named A1.1 problems for the practitioner master op."""

    if not isinstance(practitioner, dict):
        return ["practitioner probe unreadable"]
    problems = []
    link_user = practitioner.get("link_user")
    if not isinstance(link_user, str) or not link_user.strip():
        # A malformed probe target is a configuration defect; a user that is
        # merely absent is reported, because the 11B window creates it.
        problems.append("practitioner link user is not configured")
    links = practitioner.get("links")
    if not isinstance(links, dict):
        problems.append("practitioner link targets unreadable")
        return problems
    for field, rows in sorted(links.items()):
        if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
            problems.append(f"practitioner link rows unreadable for {field}")
        elif rows == 0:
            problems.append(
                f"no rows installed for the mandatory practitioner link {field}"
            )
    return problems


def boundary_sha_problems(report):
    """Return named boundary/source-hash problems from the on-site read-back."""

    if not isinstance(report, dict) or not report:
        return ["boundary report unreadable"]
    problems = []
    for family, entry in sorted(report.items()):
        if not isinstance(entry, dict):
            problems.append(f"{family} boundary unreadable")
            continue
        if entry.get("problem"):
            problems.append(f"{family}: {entry['problem']}")
        pinned = entry.get("pinned")
        if not pinned:
            continue
        computed = entry.get("source_sha256")
        if not isinstance(computed, dict):
            problems.append(f"{family} source hashes unreadable")
            continue
        for key, value in sorted(pinned.items()):
            if computed.get(key) != value:
                problems.append(f"{family} source drift for {key}")
    return problems


def selfcheck() -> int:
    """Run the pure preflight functions against fixtures; 0 only when all pass."""

    good_inventory = {
        "supplier_group": 0,
        "uom": 0,
        "warehouse": 0,
        "supplier": 0,
        "customer": 0,
        "item": 0,
        "price_list": 0,
        "mode_of_payment": 0,
        "procedure_template": 0,
        "practitioner": 0,
    }
    good_report = {
        "finance": {
            "problem": None,
            "source_sha256": {"sales_invoice": "a" * 64, "payment_entry": "b" * 64},
            "pinned": {"sales_invoice": "a" * 64, "payment_entry": "b" * 64},
        },
        "procedure": {
            "problem": None,
            "source_sha256": {"clinical_procedure": "d" * 64},
            "pinned": {"clinical_procedure": "d" * 64},
        },
        "appointment": {"problem": None},
    }
    cases = [
        (
            "an empty master inventory passes",
            not masters_order_problems(good_inventory),
        ),
        (
            "items without a price list are caught",
            bool(masters_order_problems(dict(good_inventory, item=1))),
        ),
        (
            "a seeded order passes",
            not masters_order_problems(
                dict(
                    good_inventory,
                    uom=1,
                    price_list=1,
                    item=1,
                    procedure_template=1,
                )
            ),
        ),
        (
            "templates without items are caught",
            bool(
                masters_order_problems(
                    dict(good_inventory, price_list=1, procedure_template=1)
                )
            ),
        ),
        (
            "unreadable counts are caught",
            bool(masters_order_problems(dict(good_inventory, item=None))),
        ),
        (
            "items without a uom are caught",
            bool(masters_order_problems(dict(good_inventory, item=1))),
        ),
        (
            "suppliers without a supplier group are caught",
            bool(masters_order_problems(dict(good_inventory, supplier=1))),
        ),
        (
            "the amended seed order passes",
            not masters_order_problems(
                dict(
                    good_inventory,
                    uom=1,
                    supplier_group=1,
                    supplier=1,
                    price_list=1,
                    item=1,
                    procedure_template=1,
                )
            ),
        ),
        (
            "a resolved company has no prerequisite problem",
            not masters_prerequisite_problems("SIT Synthetic Company", good_inventory),
        ),
        (
            "an unresolved company is refused",
            bool(masters_prerequisite_problems("", good_inventory)),
        ),
        (
            "an unreadable inventory is refused by the prerequisite check",
            bool(masters_prerequisite_problems("SIT Synthetic Company", None)),
        ),
        (
            "a practitioner row is order-independent of the admin chain",
            not masters_order_problems(
                dict(good_inventory, practitioner=1)
            ),
        ),
        (
            "the practitioner probe reports its links and the SIT A user",
            "'practitioner'" in masters_probe_script()
            and PRACTITIONER_LINK_USER in masters_probe_script(),
        ),
        (
            "a practitioner with no mandatory links passes",
            not practitioner_prerequisite_problems(
                {"links": {}, "link_user": PRACTITIONER_LINK_USER}
            ),
        ),
        (
            "a missing practitioner link user is a configuration refusal",
            bool(
                practitioner_prerequisite_problems(
                    {"links": {}, "link_user": ""}
                )
            ),
        ),
        (
            "a zero-row mandatory practitioner link is refused",
            bool(
                practitioner_prerequisite_problems(
                    {"links": {"gender": 0}, "link_user": PRACTITIONER_LINK_USER}
                )
            ),
        ),
        (
            "an unreadable practitioner probe is refused",
            bool(practitioner_prerequisite_problems(None)),
        ),
        (
            "an unreadable practitioner link map is refused",
            bool(practitioner_prerequisite_problems({"links": None})),
        ),
        (
            "the masters prerequisite check includes the practitioner probe",
            bool(
                masters_prerequisite_problems(
                    "SIT Synthetic Company",
                    good_inventory,
                    {"links": {"gender": 0}},
                )
            ),
        ),
        ("a pinned boundary passes", not boundary_sha_problems(good_report)),
        (
            "source drift is caught",
            bool(
                boundary_sha_problems(
                    dict(
                        good_report,
                        finance=dict(
                            good_report["finance"],
                            source_sha256={
                                "sales_invoice": "c" * 64,
                                "payment_entry": "b" * 64,
                            },
                        ),
                    )
                )
            ),
        ),
        (
            "a boundary problem is caught",
            bool(
                boundary_sha_problems(
                    dict(good_report, appointment={"problem": "versions drift"})
                )
            ),
        ),
        ("an empty report is caught", bool(boundary_sha_problems({}))),
        (
            "procedure source drift is caught",
            bool(
                boundary_sha_problems(
                    dict(
                        good_report,
                        procedure=dict(
                            good_report["procedure"],
                            source_sha256={"clinical_procedure": "e" * 64},
                        ),
                    )
                )
            ),
        ),
        (
            "the boundary probe collects the procedure pin",
            "'procedure', '_collect_procedure_boundary_snapshot'"
            in boundary_shas_probe_script(),
        ),
        (
            "the masters probe reports the two contract prerequisite kinds",
            "'uom': 'UOM'" in masters_probe_script()
            and "'supplier_group': 'Supplier Group'" in masters_probe_script(),
        ),
        (
            "the admin install probe calls the contract preflight",
            "native_admin_install_problem"
            in admin_install_probe_script()
            and "admin-install" in SITE_COMMANDS,
        ),
        (
            "a passing admin install report has no problem",
            not admin_install_problems({"problem": None}),
        ),
        (
            "a failing admin install report is refused",
            bool(
                admin_install_problems(
                    {"problem": "missing field Item Price.rate"}
                )
            ),
        ),
        (
            "an unreadable admin install report is refused",
            bool(admin_install_problems(None))
            and bool(admin_install_problems({}))
            and bool(admin_install_problems({"problem": ""})),
        ),
        (
            "a fully granted admin authority report passes",
            not admin_authority_problems(
                {
                    "email": "sit.adm@example.invalid",
                    "exists": True,
                    "authority": {
                        label: True for label in ADMIN_AUTHORITY_DOCTYPES
                    },
                }
            ),
        ),
        (
            "one missing admin authority is named",
            admin_authority_problems(
                {
                    "email": "sit.adm@example.invalid",
                    "exists": True,
                    "authority": dict(
                        {label: True for label in ADMIN_AUTHORITY_DOCTYPES},
                        supplier_group=False,
                    ),
                }
            )
            == ["missing create authority for supplier_group"],
        ),
        (
            "an unreadable admin authority reading is refused",
            bool(
                admin_authority_problems(
                    {
                        "email": "sit.adm@example.invalid",
                        "exists": True,
                        "authority": dict(
                            {label: True for label in ADMIN_AUTHORITY_DOCTYPES},
                            practitioner=None,
                        ),
                    }
                )
            ),
        ),
        (
            "an absent admin account is refused",
            bool(
                admin_authority_problems(
                    {"email": "nobody@example.invalid", "exists": False}
                )
            ),
        ),
        (
            "a malformed admin authority report is refused",
            bool(admin_authority_problems(None))
            and bool(admin_authority_problems({}))
            and bool(
                admin_authority_problems(
                    {"email": "x@example.invalid", "exists": True, "authority": None}
                )
            ),
        ),
        (
            "the admin authority probe reports every contracted DocType",
            all(
                repr(doctype) in admin_authority_probe_script("sit.adm@example.invalid")
                for doctype in ADMIN_AUTHORITY_DOCTYPES.values()
            )
            and "has_permission" in admin_authority_probe_script("x"),
        ),
        ("embedded backend probes compile", probes_compile()),
    ]
    failed = [name for name, ok in cases if not ok]
    for name, ok in cases:
        print(f"{'PASS' if ok else 'FAIL'} selfcheck {name}")
    print(f"selfcheck: {len(cases) - len(failed)}/{len(cases)} passed")
    return 0 if not failed else 1


def ok(condition: bool, message: str, detail: object = None) -> None:
    state = "PASS" if condition else "REFUSE"
    suffix = f": {detail}" if detail is not None else ""
    print(f"{state} {message}{suffix}")
    if not condition:
        failures.append(message)


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)


def in_backend(script: str) -> subprocess.CompletedProcess:
    """Run a read-only probe inside the running backend (no site writes)."""

    return compose("exec", "-T", "backend", "bash", "-c", script)


def masters_probe_script() -> str:
    """Backend probe: read-only master counts plus the resolved company.

    The company resolution mirrors the contract's own resolver (Global Defaults,
    then the frappe-16 database default, then exactly one Company row) so the
    preflight reports the same state the admin master ops will see. Amendment
    A1.1 also reports every mandatory Link field on Healthcare Practitioner with
    its installed row count, plus whether the SIT A link user exists (read-only).
    """

    doctypes = repr(dict(MASTER_DOCTYPES))
    link_user = repr(PRACTITIONER_LINK_USER)
    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json\n"
        "import frappe\n"
        f"frappe.init(site={SITE!r}, sites_path='.')\n"
        "frappe.connect()\n"
        f"doctypes = {doctypes}\n"
        f"link_user = {link_user}\n"
        "company = frappe.db.get_single_value('Global Defaults', 'default_company')\n"
        "if not company:\n"
        "    company = frappe.db.get_default('company')\n"
        "if not company:\n"
        "    rows = frappe.get_all('Company', pluck='name') or ()\n"
        "    company = rows[0] if len(rows) == 1 else None\n"
        "meta = frappe.get_meta('Healthcare Practitioner')\n"
        "links = {}\n"
        "for field in meta.fields or ():\n"
        "    if getattr(field, 'fieldtype', None) != 'Link':\n"
        "        continue\n"
        "    if not getattr(field, 'reqd', 0):\n"
        "        continue\n"
        "    if not getattr(field, 'options', None):\n"
        "        continue\n"
        "    links[field.fieldname] = int(frappe.db.count(field.options))\n"
        "print(json.dumps({\n"
        "    'company': company,\n"
        "    'masters': {kind: frappe.db.count(doctype)\n"
        "                for kind, doctype in doctypes.items()},\n"
        "    'practitioner': {\n"
        "        'links': links,\n"
        "        'link_user': link_user,\n"
        "        'link_user_exists': bool(frappe.db.exists('User', link_user)),\n"
        "    },\n"
        "}))\n"
        "PY"
    )


def finance_counts_probe_script() -> str:
    """Backend probe: read-only invoice/payment/GL/PLE counts."""

    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json\n"
        "import frappe\n"
        f"frappe.init(site={SITE!r}, sites_path='.')\n"
        "frappe.connect()\n"
        "invoices = frappe.get_all(\n"
        "    'Sales Invoice', fields=['name', 'docstatus'],\n"
        "    order_by='creation asc', limit=20,\n"
        ")\n"
        "payments = frappe.get_all(\n"
        "    'Payment Entry', fields=['name', 'docstatus'],\n"
        "    order_by='creation asc', limit=20,\n"
        ")\n"
        "def rows(doctype, voucher_type, name):\n"
        "    return frappe.db.count(\n"
        "        doctype, {'voucher_type': voucher_type, 'voucher_no': name}\n"
        "    )\n"
        "print(json.dumps({\n"
        "    'sales_invoice_total': frappe.db.count('Sales Invoice'),\n"
        "    'sales_invoice_submitted': frappe.db.count(\n"
        "        'Sales Invoice', {'docstatus': 1}\n"
        "    ),\n"
        "    'payment_entry_total': frappe.db.count('Payment Entry'),\n"
        "    'payment_entry_submitted': frappe.db.count(\n"
        "        'Payment Entry', {'docstatus': 1}\n"
        "    ),\n"
        "    'gl_entry_total': frappe.db.count('GL Entry'),\n"
        "    'payment_ledger_entry_total': frappe.db.count(\n"
        "        'Payment Ledger Entry'\n"
        "    ),\n"
        "    'invoices': [dict(row, gl_rows=rows(\n"
        "        'GL Entry', 'Sales Invoice', row['name']), ple_rows=rows(\n"
        "        'Payment Ledger Entry', 'Sales Invoice', row['name'])\n"
        "    ) for row in invoices],\n"
        "    'payments': [dict(row, gl_rows=rows(\n"
        "        'GL Entry', 'Payment Entry', row['name']), ple_rows=rows(\n"
        "        'Payment Ledger Entry', 'Payment Entry', row['name'])\n"
        "    ) for row in payments],\n"
        "}))\n"
        "PY"
    )


def boundary_shas_probe_script() -> str:
    """Backend probe: installed controller hashes against the pinned sets."""

    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json\n"
        "import frappe\n"
        "from clinic_ui.core import (\n"
        "    finance_contract, procedure_contract, purchase_contract, stock_contract,\n"
        ")\n"
        "from clinic_ui.runtime.frappe_gateway import Gateway\n"
        f"frappe.init(site={SITE!r}, sites_path='.')\n"
        "frappe.connect()\n"
        "gateway = Gateway()\n"
        "report = {}\n"
        "collectors = (\n"
        "    ('stock', '_collect_stock_boundary_snapshot',\n"
        "     stock_contract.REVIEWED_SOURCE_SHA256),\n"
        "    ('purchase', '_collect_purchase_boundary_snapshot',\n"
        "     purchase_contract.REVIEWED_SOURCE_SHA256),\n"
        "    ('finance', '_collect_finance_boundary_snapshot',\n"
        "     finance_contract.REVIEWED_SOURCE_SHA256),\n"
        "    ('procedure', '_collect_procedure_boundary_snapshot',\n"
        "     {'clinical_procedure': procedure_contract.PROCEDURE_SOURCE_SHA256}),\n"
        ")\n"
        "for family in ('appointment', 'encounter', 'procedure', 'stock',\n"
        "               'purchase', 'finance'):\n"
        "    report[family] = {\n"
        "        'problem': getattr(gateway, 'native_%s_boundary_problem' % family)(),\n"
        "    }\n"
        "for family, collector, pinned in collectors:\n"
        "    snapshot = getattr(gateway, collector)()\n"
        "    report[family]['source_sha256'] = snapshot.get('source_sha256')\n"
        "    report[family]['pinned'] = dict(pinned)\n"
        "print(json.dumps(report, default=str))\n"
        "PY"
    )


def probe_body(script: str) -> str:
    return script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def probes_compile() -> bool:
    """True when every embedded backend probe is valid Python."""

    try:
        for script in (
            masters_probe_script(),
            finance_counts_probe_script(),
            boundary_shas_probe_script(),
            admin_authority_probe_script("sit.adm@example.invalid"),
        ):
            compile(probe_body(script), "backend-probe", "exec")
        return True
    except SyntaxError:
        return False


def run_admin_install() -> int:
    """Read-only: assert the admin family's install preflight passes on site."""

    probe = in_backend(admin_install_probe_script())
    if probe.returncode != 0:
        print("REFUSE admin-install: backend probe failed")
        print(probe.stderr.strip()[-400:])
        return 1
    report = json.loads(probe.stdout)
    problems = admin_install_problems(report)
    problem = report.get("problem") if isinstance(report, dict) else None
    print(
        "INFO admin install preflight: pass"
        if problem is None
        else f"INFO admin install preflight: {problem}"
    )
    for entry in problems:
        print(f"REFUSE {entry}")
    if problems:
        return 1
    print("PREFLIGHT OK: the admin install preflight passes on this image")
    return 0


def run_admin_authority(email: str) -> int:
    """Read-only: assert one account can exercise every admin master + user op."""

    probe = in_backend(admin_authority_probe_script(email))
    if probe.returncode != 0:
        print("REFUSE admin-authority: backend probe failed")
        print(probe.stderr.strip()[-400:])
        return 1
    report = json.loads(probe.stdout)
    authority = report.get("authority") if isinstance(report, dict) else None
    if isinstance(authority, dict):
        for label in ADMIN_AUTHORITY_DOCTYPES:
            state = authority.get(label)
            if state is True:
                verdict = "granted"
            elif state is False:
                verdict = "MISSING"
            else:
                verdict = "unreadable"
            print(f"INFO admin authority {label}: {verdict}")
    problems = admin_authority_problems(report)
    for problem in problems:
        print(f"REFUSE {problem}")
    if problems:
        return 1
    print(f"PREFLIGHT OK: {email} holds native create authority for the admin surface")
    return 0


def run_masters() -> int:
    """Print the installed master inventory and refuse an impossible order."""

    probe = in_backend(masters_probe_script())
    if probe.returncode != 0:
        print("REFUSE masters: backend probe failed")
        print(probe.stderr.strip()[-400:])
        return 1
    report = json.loads(probe.stdout)
    inventory = report.get("masters") if isinstance(report, dict) else None
    company = report.get("company") if isinstance(report, dict) else None
    practitioner = (
        report.get("practitioner") if isinstance(report, dict) else None
    )
    if not isinstance(inventory, dict):
        print("REFUSE masters: master inventory unreadable")
        return 1
    problems = masters_order_problems(inventory)
    prerequisites = masters_prerequisite_problems(
        company, inventory, practitioner
    )
    if isinstance(company, str) and company.strip():
        print(f"INFO admin company: {company}")
    for kind, doctype in MASTER_DOCTYPES.items():
        count = inventory.get(kind)
        state = "absent" if count == 0 else f"present ({count})"
        if kind in ("uom", "supplier_group") and count == 0:
            state += " — the contract seeds it first (budget v3)"
        if kind == "practitioner" and count == 0:
            state += " — the contract creates it (Amendment A1.1)"
        print(f"INFO master {kind} ({doctype}): {state}")
    if isinstance(practitioner, dict):
        links = practitioner.get("links")
        if isinstance(links, dict) and links:
            for field, rows in sorted(links.items()):
                print(
                    f"INFO practitioner mandatory link {field}: {rows} row(s)"
                )
        elif isinstance(links, dict):
            print(
                "INFO practitioner mandatory links: none on this installed meta"
            )
        user = practitioner.get("link_user")
        if user:
            state = "present" if practitioner.get("link_user_exists") else (
                "absent — created in the 11B window"
            )
            print(f"INFO practitioner link user {user}: {state}")
    for problem in problems:
        print(f"REFUSE master order: {problem}")
    for problem in prerequisites:
        print(f"REFUSE master prerequisite: {problem}")
    if problems or prerequisites:
        return 1
    print("PREFLIGHT OK: master inventory is in dependency order")
    return 0


def run_finance_counts() -> int:
    """Print the finance ledger counts read-only; never opens a gate."""

    probe = in_backend(finance_counts_probe_script())
    if probe.returncode != 0:
        print("REFUSE finance-counts: backend probe failed")
        print(probe.stderr.strip()[-400:])
        return 1
    print("INFO finance counts (read-only; no gate opened):")
    print(json.dumps(json.loads(probe.stdout), indent=2, sort_keys=True))
    return 0


def run_boundary_shas() -> int:
    """Compute the installed controller hashes on-site and compare the pins."""

    probe = in_backend(boundary_shas_probe_script())
    if probe.returncode != 0:
        print("REFUSE boundary-shas: backend probe failed")
        print(probe.stderr.strip()[-400:])
        return 1
    report = json.loads(probe.stdout)
    for family, entry in sorted(report.items()):
        computed = entry.get("source_sha256") or {}
        pinned = entry.get("pinned") or {}
        for key, value in sorted(pinned.items()):
            actual = computed.get(key)
            state = "match" if actual == value else "DRIFT"
            print(f"INFO boundary sha {family}.{key}: {state}")
        if entry.get("problem") is None:
            print(f"INFO boundary {family}: pinned")
    problems = boundary_sha_problems(report)
    for problem in problems:
        print(f"REFUSE boundary: {problem}")
    if problems:
        return 1
    print("PREFLIGHT OK: every installed controller hash matches its pin")
    return 0


SITE_COMMANDS = {
    "masters": run_masters,
    "admin-install": run_admin_install,
    "finance-counts": run_finance_counts,
    "boundary-shas": run_boundary_shas,
}


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else list(argv)
    if "--selfcheck" in arguments:
        return selfcheck()
    if arguments and arguments[0] == "--help":
        print(
            "usage: preflight.py [--selfcheck | masters | admin-install | "
            "admin-authority <email> | finance-counts | boundary-shas]"
        )
        print("no argument: refuse ownership collisions for the marley-sit project")
        return 0
    if len(arguments) == 2 and arguments[0] == "admin-authority":
        return run_admin_authority(arguments[1])
    for name, run in SITE_COMMANDS.items():
        if arguments == [name]:
            return run()
    if arguments:
        print(f"REFUSE unknown argument: {' '.join(arguments)}")
        return 2

    inherited = os.environ.get("COMPOSE_PROJECT_NAME")
    print(
        f"INFO COMPOSE_PROJECT_NAME={inherited!r} (ignored; -p {PROJECT} is forced)"
        if inherited
        else f"INFO COMPOSE_PROJECT_NAME not set; using -p {PROJECT}"
    )

    rendered = compose("config", "--format", "json")
    if rendered.returncode != 0:
        print("REFUSE compose model does not render")
        return 1
    model = json.loads(rendered.stdout)
    ok(model.get("name") == PROJECT, "resolved Compose project is forced name", model.get("name"))

    project_containers = docker(
        "ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"
    ).stdout.split()
    for container_id in project_containers:
        labels = json.loads(
            docker("inspect", "--format", "{{json .Config.Labels}}", container_id).stdout
        )
        config_files = labels.get("com.docker.compose.project.config_files", "")
        resolved = [os.path.realpath(p) for p in config_files.split(",") if p]
        ok(
            str(COMPOSE_FILE) in resolved,
            "container belongs to this compose file",
            {
                "service": labels.get("com.docker.compose.service"),
                "project": labels.get("com.docker.compose.project"),
            },
        )

    project_volumes = docker(
        "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"
    ).stdout.split()
    ok(
        set(project_volumes) <= EXPECTED_VOLUMES,
        "no unexpected volumes carry this project label",
        sorted(project_volumes),
    )

    all_volumes = set(docker("volume", "ls", "-q").stdout.split())
    for name in sorted(EXPECTED_VOLUMES & all_volumes):
        labels = json.loads(
            docker("volume", "inspect", "--format", "{{json .Labels}}", name).stdout
        ) or {}
        ok(
            labels.get("com.docker.compose.project") == PROJECT,
            "expected volume is owned by this project",
            {"volume": name, "project": labels.get("com.docker.compose.project")},
        )

    project_networks = docker(
        "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"
    ).stdout.split()
    for network_id in project_networks:
        network = json.loads(docker("network", "inspect", network_id).stdout)[0]
        labels = network.get("Labels") or {}
        ok(
            labels.get("com.docker.compose.project") == PROJECT,
            "network is owned by this project",
            {"name": network.get("Name")},
        )
    if EXPECTED_NETWORK in set(docker("network", "ls", "--format", "{{.Name}}").stdout.split()):
        labels = json.loads(
            docker("network", "inspect", "--format", "{{json .Labels}}", EXPECTED_NETWORK).stdout
        ) or {}
        ok(
            labels.get("com.docker.compose.project") == PROJECT,
            "expected network name is not owned by a foreign project",
            {"network": EXPECTED_NETWORK},
        )

    listeners = docker("ps", "-q", "--filter", f"publish={PUBLISHED_PORT}").stdout.split()
    for container_id in listeners:
        labels = json.loads(
            docker("inspect", "--format", "{{json .Config.Labels}}", container_id).stdout
        )
        ok(
            labels.get("com.docker.compose.project") == PROJECT,
            "loopback port listener belongs to this project",
            {"port": PUBLISHED_PORT, "project": labels.get("com.docker.compose.project")},
        )

    projects = json.loads(docker("compose", "ls", "-a", "--format", "json").stdout) or []
    for entry in projects:
        if entry.get("Name") != PROJECT:
            continue
        config_files = [
            os.path.realpath(p) for p in (entry.get("ConfigFiles") or "").split(",") if p
        ]
        ok(
            str(COMPOSE_FILE) in config_files,
            "registered Compose project uses this compose file",
            {"project": PROJECT},
        )

    if failures:
        print(f"REFUSING DEPLOYMENT: {len(failures)} ownership collision(s)")
        return 1
    print("PREFLIGHT OK: project and resources are owned by marley-sit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
