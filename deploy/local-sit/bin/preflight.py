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

# Family-master inventory the SIT lanes seed through the admin contract.
MASTER_DOCTYPES = {
    "warehouse": "Warehouse",
    "supplier": "Supplier",
    "customer": "Customer",
    "item": "Item",
    "price_list": "Price List",
    "mode_of_payment": "Mode of Payment",
    "procedure_template": "Clinical Procedure Template",
}

failures: list[str] = []


def masters_order_problems(inventory):
    """Return named dependency-order problems for the master inventory.

    The approved seed order is price list -> item (the item master writes an
    Item Price against it) -> procedure template (which names the item).
    Warehouse, supplier and customer are reported present-or-absent, not
    asserted: the SIT lanes decide when to seed them.
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
    if counts["item"] > 0 and counts["price_list"] == 0:
        problems.append("items exist without any Price List")
    if counts["procedure_template"] > 0 and counts["item"] == 0:
        problems.append("procedure templates exist without any Item")
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
        "warehouse": 0,
        "supplier": 0,
        "customer": 0,
        "item": 0,
        "price_list": 0,
        "mode_of_payment": 0,
        "procedure_template": 0,
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
                dict(good_inventory, price_list=1, item=1, procedure_template=1)
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
    """Backend probe: read-only master counts for the dependency check."""

    doctypes = repr(dict(MASTER_DOCTYPES))
    return (
        "cd sites && ../env/bin/python - <<'PY'\n"
        "import json\n"
        "import frappe\n"
        f"frappe.init(site={SITE!r}, sites_path='.')\n"
        "frappe.connect()\n"
        f"doctypes = {doctypes}\n"
        "print(json.dumps({kind: frappe.db.count(doctype)\n"
        "                  for kind, doctype in doctypes.items()}))\n"
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
        ):
            compile(probe_body(script), "backend-probe", "exec")
        return True
    except SyntaxError:
        return False


def run_masters() -> int:
    """Print the installed master inventory and refuse an impossible order."""

    probe = in_backend(masters_probe_script())
    if probe.returncode != 0:
        print("REFUSE masters: backend probe failed")
        print(probe.stderr.strip()[-400:])
        return 1
    inventory = json.loads(probe.stdout)
    problems = masters_order_problems(inventory)
    for kind, doctype in MASTER_DOCTYPES.items():
        count = inventory.get(kind)
        state = "absent" if count == 0 else f"present ({count})"
        print(f"INFO master {kind} ({doctype}): {state}")
    for problem in problems:
        print(f"REFUSE master order: {problem}")
    if problems:
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
        print("usage: preflight.py [--selfcheck | masters | finance-counts | boundary-shas]")
        print("no argument: refuse ownership collisions for the marley-sit project")
        return 0
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
