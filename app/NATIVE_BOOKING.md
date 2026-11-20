# Reception department bookings

Reception uses the authenticated Frappe resource create endpoint. In Frappe
16.54.0, `frappe.api.v1.create_doc` calls `Document.insert`; Desk `savedocs`
calls `save`, which uses the same insert path for a new document. Marley
applies its own validation, status, check-in flag and appointment type
duration. Staff select Company rather than using a guessed default.

Return-visit and checkout links still open booking with their source cues.
Their `patient` value is a workspace reference, which may be a fixture alias;
it is not a verified native Patient Link. Staff must select the patient again.
Other historical hints (including sample suggested type) do not prefill native
fields.
