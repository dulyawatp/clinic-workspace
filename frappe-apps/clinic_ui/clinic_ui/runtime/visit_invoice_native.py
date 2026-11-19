"""Read-only native evidence; not an invoice authorisation or write path."""
from decimal import Decimal, InvalidOperation


FIELDS = {
    "Patient Appointment": {
        "appointment_for": ("Select", None), "appointment_date": ("Date", None),
        "status": ("Select", None), "practitioner": ("Link", "Healthcare Practitioner"),
        "patient": ("Link", "Patient"), "company": ("Link", "Company"),
        "invoiced": ("Check", None), "ref_sales_invoice": ("Link", "Sales Invoice"),
        "paid_amount": ("Currency", None), "mode_of_payment": ("Link", "Mode of Payment"),
        "inpatient_record": ("Link", "Inpatient Record"),
        "appointment_type": ("Link", "Appointment Type"),
        "template_dt": ("Link", "DocType"), "template_dn": ("Dynamic Link", "template_dt"),
        "procedure_template": ("Link", "Clinical Procedure Template"),
        "procedure_prescription": ("Link", "Procedure Prescription"),
        "therapy_type": ("Link", "Therapy Type"), "therapy_plan": ("Link", "Therapy Plan"),
        "service_request": ("Link", "Service Request"),
        "insurance_payor": ("Link", "Insurance Payor"),
        "insurance_policy": ("Link", "Patient Insurance Policy"),
        "insurance_coverage": ("Link", "Patient Insurance Coverage"),
    },
    "Healthcare Practitioner": {
        "user_id": ("Link", "User"), "status": ("Select", None),
        "enable_free_follow_ups": ("Check", None),
        "op_consulting_charge_item": ("Link", "Item"),
        "op_consulting_charge": ("Currency", "Currency"),
    },
    "Patient": {"customer": ("Link", "Customer")},
    "Customer": {},
    "Company": {"default_currency": ("Link", "Currency")},
    "Item": {"disabled": ("Check", None), "is_sales_item": ("Check", None)},
    "Patient Encounter": {
        "appointment": ("Link", "Patient Appointment"), "practitioner": ("Link", "Healthcare Practitioner"),
        "patient": ("Link", "Patient"), "company": ("Link", "Company"),
        "status": ("Select", None), "invoiced": ("Check", None),
    },
}
LINKS = {
    "Sales Invoice Item": {
        "reference_dt": ("Link", "DocType"),
        "reference_dn": ("Dynamic Link", "reference_dt"),
    },
    "Sales Invoice": {"patient": ("Link", "Patient")},
}


def _schema(meta, fields):
    for name, (fieldtype, options) in fields.items():
        field = meta.get_field(name)
        if field is None or field.fieldtype != fieldtype:
            return False
        if options is not None and field.options != options:
            return False
    return True


def _read(gateway, doctype, name, user):
    if not isinstance(name, str) or not name.strip():
        return None
    frappe = gateway.frappe
    meta = frappe.get_meta(doctype)
    if not _schema(meta, FIELDS[doctype]) or not gateway._fields_readable(meta, FIELDS[doctype]):
        return None
    if not frappe.has_permission(doctype, ptype="read", doc=name, user=user):
        return None
    doc = frappe.get_doc(doctype, name)
    return doc if doc.name == name else None


def _money(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def visit_invoice_evidence(gateway, appointment_id):
    """Return minimal evidence or None; internal history predicates reveal no IDs."""
    try:
        frappe = gateway.frappe
        user = frappe.session.user
        if not isinstance(user, str) or not user or user == "Guest":
            return None
        appointment = _read(gateway, "Patient Appointment", appointment_id, user)
        if appointment is None:
            return None
        if (appointment.appointment_for != "Practitioner" or appointment.status not in ("Open", "Closed")
                or str(appointment.appointment_date) != frappe.utils.today()
                or not isinstance(appointment.docstatus, int)
                or isinstance(appointment.docstatus, bool) or appointment.docstatus != 0
                or type(appointment.invoiced) is not int or appointment.invoiced != 0
                or appointment.ref_sales_invoice not in (None, "")
                or appointment.inpatient_record not in (None, "")
                or appointment.mode_of_payment not in (None, "")):
            return None
        if any(getattr(appointment, field) not in (None, "") for field in (
            "procedure_template", "procedure_prescription", "therapy_type", "therapy_plan",
            "service_request", "insurance_payor", "insurance_policy", "insurance_coverage",
        )):
            return None
        if not (appointment.template_dt in (None, "") and appointment.template_dn in (None, "")):
            if (appointment.template_dt != "Appointment Type"
                    or not isinstance(appointment.appointment_type, str)
                    or not appointment.appointment_type.strip()
                    or appointment.template_dn != appointment.appointment_type):
                return None
        practitioner = _read(gateway, "Healthcare Practitioner", appointment.practitioner, user)
        # Marley dda107b26e3a0fe1dbd65c7257371235de054a04,
        # healthcare/healthcare/doctype/fee_validity/fee_validity.py:86-99
        # permits free follow-ups when either practitioner or site enables them.
        if (practitioner is None or practitioner.status != "Active" or practitioner.user_id != user
                or type(practitioner.enable_free_follow_ups) is not int
                or practitioner.enable_free_follow_ups != 0):
            return None
        identities = frappe.get_all(
            "Healthcare Practitioner", filters={"user_id": user, "status": "Active"},
            pluck="name", limit_page_length=2,
        )
        if identities != [appointment.practitioner]:
            return None

        settings_meta = frappe.get_meta("Healthcare Settings")
        if not _schema(settings_meta, {"enable_free_follow_ups": ("Check", None)}):
            return None
        follow_ups = frappe.db.sql(
            "SELECT value FROM tabSingles WHERE doctype=%s AND field=%s LIMIT 2",
            ("Healthcare Settings", "enable_free_follow_ups"),
        )
        if len(follow_ups) != 1 or len(follow_ups[0]) != 1 or follow_ups[0][0] != "0":
            return None
        encounter_meta = frappe.get_meta("Patient Encounter")
        if not _schema(encounter_meta, FIELDS["Patient Encounter"]):
            return None
        encounter_id = frappe.db.get_value(
            "Patient Encounter", {"appointment": appointment_id}, "name", ignore=False, order_by=None
        )
        if appointment.status == "Open":
            if encounter_id is not None:
                return None
        else:
            if not isinstance(encounter_id, str) or not encounter_id.strip():
                return None
            if frappe.db.get_value(
                "Patient Encounter", {"appointment": appointment_id, "name": ["!=", encounter_id]},
                "name", ignore=False, order_by=None,
            ) is not None:
                return None
            encounter = _read(gateway, "Patient Encounter", encounter_id, user)
            if (encounter is None or encounter.appointment != appointment_id
                    or encounter.practitioner != appointment.practitioner
                    or encounter.patient != appointment.patient or encounter.company != appointment.company
                    or encounter.status not in ("Open", "Ordered", "Completed")
                    or not isinstance(encounter.docstatus, int) or isinstance(encounter.docstatus, bool)
                    or encounter.docstatus not in (0, 1)
                    or type(encounter.invoiced) is not int or encounter.invoiced != 0):
                return None

        patient = _read(gateway, "Patient", appointment.patient, user)
        company = _read(gateway, "Company", appointment.company, user)
        if patient is None or company is None:
            return None
        customer = _read(gateway, "Customer", patient.customer, user)
        item = _read(gateway, "Item", practitioner.op_consulting_charge_item, user)
        if customer is None or item is None:
            return None
        if (type(item.disabled) is not int or item.disabled != 0
                or type(item.is_sales_item) is not int or item.is_sales_item != 1
                or not isinstance(company.default_currency, str) or not company.default_currency.strip()):
            return None
        price = _money(practitioner.op_consulting_charge)
        paid = _money(appointment.paid_amount) if appointment.paid_amount not in (None, "") else Decimal(0)
        if price is None or price <= 0 or paid is None or paid not in (Decimal(0), price):
            return None

        for doctype, fields in LINKS.items():
            if not _schema(frappe.get_meta(doctype), fields):
                return None
        header = frappe.get_meta("Sales Invoice").get_field("appointment")
        if header is not None and (header.fieldtype != "Link" or header.options != "Patient Appointment"):
            return None
        if frappe.db.get_value("Sales Invoice Item", {
            "reference_dt": "Patient Appointment", "reference_dn": appointment_id,
        }, "name", ignore=False, order_by=None):
            return None
        if encounter_id and frappe.db.get_value("Sales Invoice Item", {
            "reference_dt": "Patient Encounter", "reference_dn": encounter_id,
        }, "name", ignore=False, order_by=None):
            return None
        if header is not None and frappe.db.get_value(
            "Sales Invoice", {"appointment": appointment_id}, "name", ignore=False, order_by=None
        ):
            return None
        evidence = {
            "metadata_verified": True, "record_fields_readable": True,
            "billing_history_complete": True, "prior_billing": False,
            "appointment_id": appointment_id, "practitioner": practitioner.name,
            "actor_practitioner": practitioner.name, "status": appointment.status,
            "docstatus": int(appointment.docstatus), "invoiced": appointment.invoiced,
            "ref_sales_invoice": appointment.ref_sales_invoice, "patient": patient.name,
            "customer": customer.name, "company": company.name,
            "currency": company.default_currency, "service": item.name, "price": price,
        }
        if encounter_id:
            evidence["encounter_id"] = encounter_id
        return evidence
    except Exception:
        return None
