# Open saved encounters in the existing workflow

Use the approved Doctor Encounter design already represented by `EncounterLayout`
on the existing `/clinic/visits/:id` route. Staff must be able to open a saved
encounter from the patient's existing Visits/history view, including a fresh
unlinked draft created by the current Save Draft action. Do not add another page.
This replaces the existing encounter screen, rather than offering another
layout as an option. The create confirmation should link to the same
saved-encounter route; it does not replace that route.
The saved-encounter screen must link back to the same patient's existing
Visit history. Use the patient identity returned by the authorized encounter
read, not a patient supplied by the route or query string.

Show real patient, practitioner, visit date and status, and supported existing
encounter content from Frappe. Distinguish a new, empty draft from unavailable
clinical data. Retain Clinical Note, Medications, photos and Lab Results placements
from the approved layout; mark any unconnected content accurately. Do not project
sample records into native mode, add permission gates on top of Frappe, or change
the behavior of existing edit, submit, prescribing or file actions.

Keep the native read boundary under `clinic_ui`, consistent with the application
contract. A direct Frappe resource read was considered because draft readback
already uses it, but a narrow read-only DTO avoids exposing or depending on the
whole native document. Use ordinary Frappe permission-aware queries and explicit
document read checks; the API layer is not an independent authorization policy.
