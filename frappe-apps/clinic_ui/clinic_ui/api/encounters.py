"""Native encounter draft write and permission-respecting read methods.

Frappe wraps successful results in ``{"message": ...}``. ``list_for_patient``
returns ``{"items": [summary, ...], "nextOffset": int | null}``; ``view``
returns a summary plus ``symptoms`` and ``diagnoses`` (lists of strings or
null when field access/schema cannot be established). A summary contains
``id``, ``patientId`` and ``patientName`` strings, ``practitioner`` and
``encounterDate`` strings or null, and integer ``docstatus``. The offset is
bounded to 0..1000000 and pages contain at most 20 rows. Invalid or
unauthorized reads raise Frappe's permission error (403); a missing detail
uses Frappe's native not-found response (normally 404), and unexpected
framework errors retain their native status (normally 500).

Patient Encounter and related Patient document read authority and displayed
field permlevels/masks are checked using Frappe. Child field access follows
the parent document's permlevels; this needs verification against the installed
Frappe version before deployment.
The native detail document check matches the framework document check, while
the additional related-Patient check and fail-closed projected-field checks
are narrower than a generic Frappe resource read.
"""

import frappe

from clinic_ui.api.patients import _body
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def create_draft(**form):
    return Gateway().create_unlinked_encounter_draft(_body(form))


@frappe.whitelist(methods=["GET"])
def list_for_patient(patient_ref=None, offset=0):
    return Gateway().list_patient_encounters(patient_ref, offset)


@frappe.whitelist(methods=["GET"])
def view(encounter_id=None):
    return Gateway().view_patient_encounter(encounter_id)
