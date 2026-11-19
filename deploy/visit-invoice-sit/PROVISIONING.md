# Visit invoice fixture provisioning — blocked runbook

This is preparation, not authority to operate. Do not execute site, Docker,
permission, configuration, or network operations until separately approved
after the suite and push. Scope is exclusively Compose project
`clinic-visit-invoice-disposable`, site `clinic-visit-invoice.local`. Follow
`README.md` in this directory for isolation, image provenance, private state,
and collision checks; never use the inherited Marley provisioner. No step
here enables invoice submission.

## Blocking metadata gap

No sound executable fixture provisioner can be derived from this checkout
alone. The pinned native app source and installed site metadata are not present
here: the image is ERPNext `v16.52.1` and the Dockerfile fetches healthcare
`v16.6.1` commit `dda107b26e3a0fe1dbd65c7257371235de054a04`.
Required fields, naming series, setup wizard state, mandatory linked records,
company chart creation, appointment validation and effective permissions must
be checked against those exact installed versions before a write script is
approved. Do not guess field defaults or copy a production chart. In
particular, a Customer may be created by native Patient hooks; resolve that
behavior before writing rather than creating duplicate parties. This runbook
is not an executable provisioning recipe and does not claim acceptance.

## Dependency order after separate authorization

1. Verify exact project and site, fresh distinct volumes and no collisions
   before site creation, per `README.md`. Reject existing resources rather
   than deleting or reusing them. Confirm image digest, pinned source and
   versions. Verify the actual site context before *every* write; refuse any
   site name other than `clinic-visit-invoice.local`. Inspect privately and
   refuse any preexisting target identities, fixture names, appointments,
   companies or financial transactions; no overwrite, repair, or deletion.
   Fresh-site setup may create native reference records; inventory and validate
   them rather than assuming the database is empty. Stop on any ambiguity.
2. On the newly created exact site, verify installed apps using
   `bench --site clinic-visit-invoice.local list-apps`. If missing, install
   only the approved pinned image's apps, in dependency order:
   `bench --site clinic-visit-invoice.local install-app erpnext`,
   `bench --site clinic-visit-invoice.local install-app healthcare`, then
   `bench --site clinic-visit-invoice.local install-app clinic_ui`. Skip an
   already installed app; do not reinstall. Each command is a site write and
   requires separate authorization. Check versions and installed schema first.
3. Before writes, inspect native metadata and controllers for Company, Account,
   Cost Center, Price List, Selling Settings, Customer Group, Customer, Item,
   UOM, User, Role, Healthcare Practitioner, Patient, Patient Appointment and
   Healthcare Settings. Compare field types/options against `FIELDS` and `LINKS`
   in `runtime/visit_invoice_native.py`, `_FIELDS`, `_INTEGRITY_FIELDS` and
   `_SWITCHES` in `runtime/visit_invoice_accounting.py`, and preview/posting
   guards. Stop if any field, hook, required value or permission is unknown.
   Develop and review exact native create commands against that metadata before
   continuing; no commands in this document imply such review occurred.
4. Create a uniquely named synthetic company with USD default currency, using
   the pinned native company/chart setup. Its default receivable account must
   be enabled, non-group, company-owned, USD, root `Asset`, type `Receivable`;
   its distinct default income account must be enabled, non-group,
   company-owned, USD, root `Income`, type `Income Account`, `Direct Income`
   or `Indirect Income`. Its cost center must be enabled, non-group and owned
   by that company. Verify native accounting and fiscal-year prerequisites;
   do not infer chart names or silently substitute accounts.
5. Create an enabled selling USD Price List and set Selling Settings'
   `selling_price_list` to it. Customer and Customer Group default price lists
   must be empty. Explicitly persist each of these singleton Check values as
   raw `tabSingles` string `0`, exactly one row each: Healthcare Settings
   `enable_free_follow_ups`, `show_payment_popup`,
   `create_lab_test_on_si_submit`, `create_observation_on_si_submit`; Selling
   Settings `enable_discount_accounting`. Verify metadata and raw rows after
   native saves. Refuse conflicts rather than removing existing data.
6. Create one enabled sales Item, not stock, not fixed asset, no variant, with
   enabled native stock UOM. Do **not** create an Item Price: the current
   accounting guard rejects *any* Item Price for this Item, even a USD 100
   price. Create one enabled synthetic System User with private credentials,
   and one active Healthcare Practitioner uniquely bound by `user_id` to that
   exact doctor, with `op_consulting_charge_item` set to the Item and
   `op_consulting_charge` exactly `100.00`. Confirm exactly one active
   practitioner for the user. Use `.invalid` identity and suppress welcome
   email. Read credentials from owner-only private storage without displaying
   them; never embed them in commands, arguments, logs, or repository files.
7. Create three distinct fictional Patients and their three distinct enabled
   Customers, each linked through `Patient.customer`; verify native hooks and
   party creation before doing so. Customer currency must be empty or USD.
   For each, create one distinct current-site-date `Open`, draft, uninvoiced
   Practitioner appointment for the exact doctor and synthetic company.
   No payment mode, inpatient, procedure, therapy, service request or insurance
   links; no sales invoice reference; paid amount zero; empty template fields
   unless the exact Appointment Type mapping in `visit_invoice_native.py` is
   satisfied. Verify no linked encounter or previous invoice item/header.
   Do not use real patient or account data. Native date means site
   `frappe.utils.today()`, not the operator's workstation date.
8. Refuse if any matching customer/company GL Entry or Party Account override
   exists for customer, customer group, practitioner, or Healthcare Settings
   income account. Refuse *any* Sales Taxes and Charges Template, Tax Rule,
   Pricing Rule, Promotional Scheme or Item Tax row site-wide, and any Item
   Price for the fixture Item. These broad predicates are intentional current
   guard behavior; do not delete conflicts. Verify 2-decimal native invoice
   and item money precision and all calculated preview/posting invariants in
   `visit_invoice_preview.py` and `visit_invoice_posting.py`.
9. Review actual metadata permission levels and native permissions. Prepare an
   independent custom least-privilege role for this synthetic doctor only:
   read access to each document and required field/permlevel in native and
   accounting guards, plus Sales Invoice read/create/write/submit and required
   item fields. Check appointment and encounter permissions required by
   posting. Verify each via native `has_permission` for the actual user and
   records, including Sales Invoice create/write/submit individually; ensure
   no role inherits broader privileges. Never assign `Accounts User`, System
   Manager, or another broad role as a shortcut. Record any permissions needed
   by native hooks for review; do not bypass with `ignore_permissions`.
10. Keep `clinic_ui_visit_invoice_write_enabled` absent or false. Do not set
    `clinic_ui_visit_invoice_write_user`, discount delegation or preview
    secret as an implicit enablement path. Any future feature enablement,
    role grant or submit requires a separate explicit approval and independently
    reviewed commands. Do not submit an invoice during provisioning.

Record only non-secret synthetic fixture identifiers and verification results
in owner-only private evidence. On conflict or incomplete native knowledge,
stop and report the blocker; do not reset the site or retry writes blindly.
Native state and live acceptance remain unverified.
