"""Private author-only committed drawing reader; not a public endpoint."""

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field

import frappe

from clinic_photos.access.draft_identity import visit_draft_name
from clinic_photos.publication import journal, staging, writer
from clinic_photos.publication import visit_annotation_journal_codec as codec
from clinic_photos.publication import visit_annotation_domain as domain
from clinic_photos.publication.validation import MAX_ANNOTATION_BYTES
from clinic_photos.publication.visit_annotation_intent import VisitAnnotationBinding, VisitAnnotationIntent
from clinic_photos.visit import actor, pair, runtime


@dataclass(frozen=True)
class ReadTarget:
    draft_name: str = field(repr=False)
    site: str = field(repr=False)
    actor: str = field(repr=False)
    appointment: str = field(repr=False)
    patient: str = field(repr=False)
    slot: str = field(repr=False)
    appointment_creation: str = field(repr=False)
    patient_creation: str = field(repr=False)
    published_slot: str = field(repr=False)
    original_photo: str = field(repr=False)
    original_file: str = field(repr=False)
    original_operation: str = field(repr=False)


@dataclass(frozen=True)
class ReadMetadata(ReadTarget):
    version_name: str = field(repr=False)
    version_number: int
    annotation_file: str = field(repr=False)
    storage_key: str = field(repr=False)


@dataclass(frozen=True)
class ReadResult:
    version_number: int
    annotation: dict | None = field(repr=False)


def _deny():
    frappe.throw("Visit annotation is not available", frappe.PermissionError)


def _row(doctype, name, fields):
    row = frappe.db.get_value(doctype, name, list(fields), as_dict=True)
    if row is not None and not isinstance(row, Mapping):
        _deny()
    return row


def resolve_read_target(appointment_name, slot):
    """Resolve private original and author authority, independent of draft state."""
    site = frappe.local.site
    conf = frappe.local.conf
    if (not runtime.enabled_for_site(site, conf) or
            conf.get("clinic_visit_photo_annotations_enabled") is not True or
            type(getattr(frappe.db, "transaction_writes", None)) is not int or
            frappe.db.transaction_writes != 0 or
            not isinstance(appointment_name, str) or not appointment_name or
            appointment_name.strip() != appointment_name or not appointment_name.isprintable() or
            slot not in ("Before", "After")):
        _deny()
    user = frappe.session.user
    if not isinstance(user, str) or not user or user in ("Guest", "Administrator"):
        _deny()
    if actor.require_visit_actor(doctor=True, for_update=False) != user:
        _deny()
    appointment = frappe.get_doc("Patient Appointment", appointment_name)
    if (appointment.name != appointment_name or not appointment.patient or
            not appointment.creation or
            not frappe.has_permission("Patient Appointment", "read", doc=appointment)):
        _deny()
    patient = frappe.get_doc("Patient", appointment.patient)
    if (patient.name != appointment.patient or not patient.creation or
            not frappe.has_permission("Patient", "read", doc=patient)):
        _deny()
    linked = pair._read_slot_structure(appointment, patient, slot)
    if not linked:
        _deny()
    record, photo, original_file = linked
    pair._require_native_read_for_verified_slot(patient.name, photo, original_file, user, site)
    name = visit_draft_name(site, appointment_name, slot, photo.name, user)
    if frappe.session.user != user or frappe.local.site != site:
        _deny()
    return ReadTarget(name, site, user, appointment_name, patient.name, slot,
                      str(appointment.creation), str(patient.creation), record.name,
                      photo.name, original_file.name, record.operation)


def resolve_read_metadata(appointment_name, slot, *, version=None):
    """Resolve native read authority and immutable version metadata, not bytes."""
    if version is not None and (type(version) is not int or version < 1):
        _deny()
    target = resolve_read_target(appointment_name, slot)
    name, user, site = target.draft_name, target.actor, target.site
    draft = _row(domain._DRAFT, name, domain._DRAFT_FIELDS)
    if draft is None:
        if version is not None:
            _deny()
        return None
    if (draft.get("name") != name or draft.get("author") != user or
            draft.get("owner") != user or draft.get("photo") != target.original_photo or
            type(draft.get("current_version_number")) is not int or
            draft["current_version_number"] < 0):
        _deny()
    if draft["current_version_number"] == 0:
        if version is not None or draft.get("current_version") not in (None, ""):
            _deny()
        return None
    if draft.get("current_version") != f'{name}-v{draft["current_version_number"]}':
        _deny()
    number = draft["current_version_number"] if version is None else version
    if number > draft["current_version_number"]:
        _deny()
    version_name = f"{name}-v{number}"
    row = _row(domain._VERSION, version_name, domain._VERSION_FIELDS)
    if (row is None or row.get("name") != version_name or row.get("draft") != name or
            type(row.get("version_number")) is not int or row["version_number"] != number or
            row.get("state") != "committed" or
            not isinstance(row.get("annotation_file"), str) or not row["annotation_file"] or
            not isinstance(row.get("storage_key"), str) or not row["storage_key"]):
        _deny()
    if frappe.session.user != user or frappe.local.site != site:
        _deny()
    return ReadMetadata(**vars(target), version_name=version_name, version_number=number,
                        annotation_file=row["annotation_file"], storage_key=row["storage_key"])


def _read_file_bytes(site, key, size, identity):
    """Open exact site tree without symlinks and verify all identities twice."""
    if not all(getattr(os, name, 0) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")):
        raise ValueError
    def bound_root():
        if frappe.local.site != site:
            raise ValueError
        for parts in (("private", "files"), ("private", staging.STAGING_DIRECTORY)):
            path = frappe.get_site_path(*parts)
            if not isinstance(path, str) or not path or os.pardir in path.split(os.sep):
                raise ValueError
        site_root = staging._require_bound_site_root()
        files = staging._absolute_staging_path(frappe.get_site_path("private", "files"))
        if os.path.basename(site_root) != site or files != os.path.join(site_root, "private", "files"):
            raise ValueError
        return os.path.dirname(site_root)

    root = bound_root()
    basename = writer._relative_basename(key)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    descriptors = []
    try:
        root_fd = os.open(root, directory_flags)
        descriptors.append(root_fd)
        chain = [(root_fd, root, None)]
        parent = root_fd
        for part in (site, "private", "files"):
            fd = os.open(part, directory_flags, dir_fd=parent)
            descriptors.append(fd)
            chain.append((fd, part, parent))
            parent = fd
        fd = os.open(basename, file_flags, dir_fd=parent)
        descriptors.append(fd)

        def check():
            for child, name, parent_fd in chain:
                opened = os.fstat(child)
                entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(entry.st_mode) or
                        (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino)):
                    raise ValueError
            opened = os.fstat(fd)
            entry = os.stat(basename, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(entry.st_mode) or
                    (opened.st_dev, opened.st_ino, opened.st_size) !=
                    (entry.st_dev, entry.st_ino, entry.st_size) or opened.st_size != size or
                    identity != f"{opened.st_dev}:{opened.st_ino}" or
                    bound_root() != root or frappe.local.site != site):
                raise ValueError
            return (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)

        before = check()
        chunks = []
        remaining = size + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) != size or check() != before:
            raise ValueError
        return data
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_committed(appointment, slot, *, version=None, operation=None, request_key=None):
    """Read one authorized committed drawing; never return private identities."""
    try:
        proof = resolve_read_metadata(appointment, slot, version=version)
        if any(value is not None and (type(value) is not str or not value or value != value.strip())
               for value in (operation, request_key)):
            raise ValueError
        if proof is None:
            if (operation is not None or request_key is not None or
                    resolve_read_metadata(appointment, slot, version=version) is not None):
                raise ValueError
            return ReadResult(0, None)
        fields = sorted(codec._ALLOWED_FIELDS - codec._NATIVE_ENVELOPE_FIELDS - {"doctype",
            "annotation_json", "synthetic_patient", "image_base64"})
        def operation_rows():
            return frappe.db.get_all(journal.OPERATION_DOCTYPE,
                filters={"published_domain_doctype": domain._VERSION,
                         "published_domain_name": proof.version_name},
                fields=["name", *fields], limit_page_length=2)

        rows = operation_rows()
        if len(rows) != 1:
            raise ValueError
        row = dict(rows[0])
        row["doctype"] = journal.OPERATION_DOCTYPE
        if ((operation is not None and row.get("name") != operation) or
                (request_key is not None and row.get("request_key") != request_key)):
            raise ValueError
        binding = VisitAnnotationBinding(**json.loads(row["visit_binding_json"]))
        for field_name in ("site", "actor", "appointment", "patient", "slot",
                           "appointment_creation", "patient_creation", "published_slot",
                           "original_photo", "original_file", "original_operation"):
            if getattr(binding, field_name) != getattr(proof, field_name):
                raise ValueError
        size = row["expected_size"]
        if (type(size) is not int or not 0 < size <= MAX_ANNOTATION_BYTES or
                row["reservation_key"] != proof.storage_key or
                row["published_file"] != proof.annotation_file or
                writer._alias_holders(proof.storage_key) != {proof.annotation_file}):
            raise ValueError
        file = _row("File", proof.annotation_file, domain._FILE_FIELDS)
        if not file:
            raise ValueError
        expected = {"name": proof.annotation_file, "file_url": proof.storage_key,
                    "file_name": proof.storage_key.rsplit("/", 1)[-1], "owner": proof.actor,
                    "is_private": 1, "file_size": size, "attached_to_doctype": None,
                    "attached_to_name": None, "attached_to_field": None}
        if any(type(file.get(k)) is not type(v) or file.get(k) != v for k, v in expected.items()):
            raise ValueError
        data = _read_file_bytes(proof.site, proof.storage_key, size, row["creation_identity"])
        if (hashlib.sha256(data).hexdigest() != row["expected_sha256"] or
                hashlib.md5(data, usedforsecurity=False).hexdigest() != file.get("content_hash")):
            raise ValueError
        intent = VisitAnnotationIntent(request_key=row["request_key"],
            expected_version=row["expected_version"], binding=binding,
            canonical_visit_annotation=data.decode("utf-8"))
        if (intent.managed_draft != proof.draft_name or intent.expected_version + 1 != proof.version_number or
                codec.load_published(row, intent=intent, operation=row["name"],
                    published_file=proof.annotation_file, creation_identity=row["creation_identity"],
                    committed_bytes=data) != intent):
            raise ValueError
        if (resolve_read_metadata(appointment, slot, version=proof.version_number) != proof or
                _row("File", proof.annotation_file, domain._FILE_FIELDS) != file or
                writer._alias_holders(proof.storage_key) != {proof.annotation_file} or
                operation_rows() != rows):
            raise ValueError
        return ReadResult(proof.version_number, json.loads(intent.canonical_visit_annotation))
    except Exception:
        _deny()
