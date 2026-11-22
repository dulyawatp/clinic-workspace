app_name = "clinic_photos"
app_title = "Clinic Photos"
app_publisher = "Clinic OpenDesign"
app_description = "Native-first protection for clinical photo drafts"
app_email = ""
app_license = "MIT"
app_version = "0.0.1"

# Supported File extension seam. The mixin only narrows native behaviour for
# protected bytes; unrelated Files keep native methods and permissions.
extend_doctype_class = {
    "File": "clinic_photos.access.file_mixin.ProtectedFileMixin",
}

# Restrictive File metadata hooks. The query condition covers `frappe.get_list`;
# privileged `frappe.get_all` bypasses it by design, so internal feature lookups
# stay scoped to one storage key and never return their rows to a client.
permission_query_conditions = {
    "File": "clinic_photos.access.permissions.file_permission_query_conditions",
    "Clinic Photo": "clinic_photos.access.permissions.photo_permission_query_conditions",
    "Clinic Photo Draft": "clinic_photos.access.permissions.draft_permission_query_conditions",
    "Clinic Photo Annotation Version": "clinic_photos.access.permissions.version_permission_query_conditions",
}
has_permission = {
    "File": "clinic_photos.access.permissions.file_has_permission",
    "Clinic Photo": "clinic_photos.access.permissions.photo_has_permission",
    "Clinic Photo Draft": "clinic_photos.access.permissions.draft_has_permission",
    "Clinic Photo Annotation Version": "clinic_photos.access.permissions.version_has_permission",
}

# Narrow external raw-reader overrides. Only the two installed callables are
# replaced. The bank wrapper preserves its callable signature and POST-only whitelist
# contract, delegates unrelated inputs unchanged, and refuses protected or approved
# ambiguous matches before the raw filesystem helper runs. The ZIP wrapper marks
# protected entries before native prefilter, then delegates entirely to native
# authorization/streaming. The Chart of Accounts importer is untouched: its local
# helper already resolves through File methods.
override_whitelisted_methods = {
    "healthcare.healthcare.doctype.patient_appointment.patient_appointment."
    "check_in_appointment": "clinic_photos.visit.checkin.check_in_appointment",
    "erpnext.accounts.doctype.bank_statement_import.bank_statement_import."
    "convert_mt940_to_csv": "clinic_photos.access.entrypoints.convert_mt940_to_csv",
    "frappe.core.api.file.zip_files": "clinic_photos.access.entrypoints.zip_files",
}

# Supported request hooks. `before_request` performs the one narrow direct-path
# classification needed before a response exists; `after_request` only applies
# no-store headers to a server-owned marker and is never an authorization boundary
# (Frappe catches its exceptions).
before_request = [
    "clinic_photos.access.entrypoints.mark_direct_private_request",
]
after_request = [
    "clinic_photos.access.entrypoints.after_request",
]

# Automatic repair for lost after-commit notifications. The existing scheduler runs
# this minute hook; it only enqueues opaque SUBMITTED identities (never publishes or
# resumes PREPARED work). No new service or system cron is introduced.
scheduler_events = {
    "cron": {
        "* * * * *": [
            "clinic_photos.publication.launcher.rediscover_submissions",
        ],
    },
}

doc_events = {
    "DocShare": {
        "validate": "clinic_photos.access.share.validate_docshare",
    },
}
