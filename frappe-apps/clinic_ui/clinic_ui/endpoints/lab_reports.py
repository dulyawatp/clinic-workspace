"""Private appointment report operations; native File permissions remain authoritative."""

from urllib.parse import parse_qs, unquote, urlsplit

from clinic_ui.core.text import is_valid_identifier


PAGE_SIZE = 20
MAX_OFFSET = 1000000
FOLDER = "Home/Lab Reports"


def _identifier(value):
    if not isinstance(value, str) or not is_valid_identifier(value):
        raise ValueError("invalid identity")
    return value


def _offset(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("invalid offset")
    if isinstance(value, str):
        if len(value) > 7 or not value.isascii() or not value.isdecimal():
            raise ValueError("invalid offset")
        value = int(value)
    if not 0 <= value <= MAX_OFFSET:
        raise ValueError("invalid offset")
    return value


def _appointment(gateway, reference):
    name = gateway.linked_resolve(_identifier(reference))
    return _appointment_by_name(gateway, name)


def _appointment_by_name(gateway, name):
    doc = gateway.frappe.get_doc("Patient Appointment", name)
    doc.check_permission("read")
    gateway._linked_fields("Patient Appointment", ("patient",))
    if not doc.patient:
        raise ValueError("missing patient")
    gateway.frappe.get_doc("Patient", doc.patient).check_permission("read")
    return doc


def _project(doc, appointment):
    if (not doc.is_private or doc.is_folder or doc.folder != FOLDER
            or doc.attached_to_doctype != "Patient Appointment"
            or doc.attached_to_name != appointment.name):
        raise ValueError("invalid file")
    doc.check_permission("read")
    url = doc.unique_url
    parts = urlsplit(url)
    path = unquote(parts.path)
    filename = path.removeprefix("/private/files/")
    if (parts.scheme or parts.netloc or not path.startswith("/private/files/")
            or not filename or filename in (".", "..") or "/" in filename
            or "\\" in filename or any(ord(c) < 32 or ord(c) == 127 for c in filename)
            or parts.fragment
            or parse_qs(parts.query, keep_blank_values=True) != {"fid": [doc.name]}):
        raise ValueError("invalid file URL")
    return {"id": doc.name, "name": doc.file_name, "sizeBytes": doc.file_size,
            "uploadedBy": doc.owner, "uploadedAt": str(doc.creation), "downloadUrl": url}


def _list(gateway, appointment, offset):
    offset = _offset(offset)
    gateway._linked_fields("File", ("file_name", "file_size", "is_private", "is_folder",
                                    "folder", "attached_to_doctype", "attached_to_name", "file_url"))
    rows = gateway.frappe.get_list("File", filters={
        "attached_to_doctype": "Patient Appointment", "attached_to_name": appointment.name,
        "is_private": 1, "is_folder": 0, "folder": FOLDER,
    }, fields=["name"], order_by="creation desc, name desc", start=offset, page_length=PAGE_SIZE + 1)
    items = [_project(gateway.frappe.get_doc("File", row["name"]), appointment) for row in rows]
    return {"items": items[:PAGE_SIZE],
            "nextOffset": offset + PAGE_SIZE if len(items) > PAGE_SIZE and offset + PAGE_SIZE <= MAX_OFFSET else None}


def list_for_appointment(gateway, appointment_ref, offset=0):
    return _list(gateway, _appointment(gateway, appointment_ref), offset)


def list_for_encounter(gateway, encounter_id, offset=0):
    encounter = gateway.frappe.get_doc("Patient Encounter", _identifier(encounter_id))
    encounter.check_permission("read")
    gateway._linked_fields("Patient Encounter", ("appointment", "patient"))
    if not encounter.appointment or not encounter.patient:
        raise ValueError("missing encounter identity")
    appointment = _appointment(gateway, encounter.appointment)
    if appointment.patient != encounter.patient:
        raise ValueError("encounter patient mismatch")
    return _list(gateway, appointment, offset)


def validate_upload(filename, content):
    if (not isinstance(filename, str) or not filename or len(filename) > 255
            or filename in (".", "..") or "/" in filename or "\\" in filename
            or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
        raise ValueError("invalid filename")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    signatures = {"pdf": b"%PDF-", "jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff",
                  "png": b"\x89PNG\r\n\x1a\n"}
    if extension not in signatures or not content.startswith(signatures[extension]):
        raise ValueError("invalid file content")


def upload(gateway, appointment_ref, filename, content):
    validate_upload(filename, content)
    appointment = _appointment(gateway, appointment_ref)
    appointment.check_permission("write")
    gateway._linked_fields("File", ("file_name", "file_size", "is_private", "is_folder",
                                    "folder", "attached_to_doctype", "attached_to_name", "file_url"))
    folder = gateway.frappe.get_doc("File", FOLDER)
    if not folder.is_folder:
        raise ValueError("invalid report folder")
    doc = gateway.insert_lab_report({
        "doctype": "File", "file_name": filename, "content": content,
        "is_private": 1, "folder": FOLDER, "attached_to_doctype": "Patient Appointment",
        "attached_to_name": appointment.name,
    })
    return _project(doc, appointment)
