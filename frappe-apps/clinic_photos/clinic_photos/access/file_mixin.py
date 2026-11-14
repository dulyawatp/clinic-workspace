"""File extension mixin registered through ``extend_doctype_class``.

The mixin narrows native File behaviour for protected bytes and delegates to
native implementations otherwise. Guards run before every side effect: the
in-memory content return (``get_content``), disk writes (``write_file``,
``save_file_on_filesystem``), native ``save_file`` transformations, and the
privacy transition (``handle_is_private_changed``). Normal download behaviour is
unchanged and still requires native authorization.

``set_is_private`` is intentionally **not** overridden: installed Frappe 16.53
defines ``set_is_private(self)`` with no argument and infers privacy from the URL.
The actual byte/alias move happens in ``handle_is_private_changed`` and is guarded
there.

Ordering contract relied on here (installed evidence: ``model/document.py`` calls
``before_insert`` before naming and sets ``flags.in_insert`` around
``run_before_save_methods``): a new File only registers inside the bound pending
context, later saves compare against the persisted row.
"""

from clinic_photos.access.frappe_context import (
    confirm_pending_cleanup,
    end_content_write,
    protected_cleanup_execution,
    require_content_write,
    require_derivative,
    require_file_delete,
    require_file_delete_on_disk,
    require_file_insert,
    require_file_persisted_identity,
    require_file_rollback,
    require_file_update,
    require_private_transition,
    require_protected_read,
)


class ProtectedFileMixin:
    """Restrict protected File download, content, lifecycle and derivatives."""

    def get_content(self, encodings=None):
        # Native ``get_content`` has no authorisation check and may return
        # ``self.content`` early, so the feature restriction runs first.
        require_protected_read(self)
        return super().get_content(encodings)

    def is_downloadable(self):
        require_protected_read(self)
        return super().is_downloadable()

    def before_insert(self):
        require_file_insert(self)
        native_before_insert = getattr(super(), "before_insert", None)
        if native_before_insert is not None:
            native_before_insert()

    def insert(self, *args, **kwargs):
        # The native insert sequence (before_insert -> validate -> db_insert ->
        # after_insert/on_update hooks) can still change the destination after the
        # pre-`db_insert` check. Verify the persisted identity and references after the
        # complete sequence; a violation raises so the caller rolls back. The row may
        # already be staged in this uncommitted transaction.
        result = super().insert(*args, **kwargs)
        require_file_persisted_identity(self)
        return result

    def validate(self):
        inserting = bool(getattr(self.flags, "in_insert", False)) or self.is_new()
        previous = None if inserting else self.get_doc_before_save()
        require_file_update(self, previous, inserting=inserting)
        super().validate()

    def save_file(
        self,
        content=None,
        decode=False,
        ignore_existing_file_check=False,
        overwrite=False,
    ):
        from clinic_photos.publication import writer

        if writer.owns_first_object(self):
            # The only place protected first bytes are created. Native create
            # permission and the ``before_insert`` validations have already run, and
            # this specialization resolves the effective write hooks, applies the
            # native size check and writes the exact object itself. Native EXIF/suffix/
            # URL rewriting, dedup retargeting and native byte writing are not reached,
            # and there is no fallback to them.
            return writer.save_first_object(
                self,
                content=content,
                decode=decode,
                ignore_existing_file_check=ignore_existing_file_check,
                overwrite=overwrite,
            )

        require_content_write(self, "save_file")
        try:
            result = super().save_file(
                content,
                decode,
                ignore_existing_file_check,
                overwrite,
            )
        except BaseException:
            # A failure never mints a receipt, so a partial write stays denied until
            # authorized cleanup.
            end_content_write(self, "save_file", failed=True)
            raise
        end_content_write(self, "save_file")
        return result

    def validate_duplicate_entry(self, *args, **kwargs):
        # Native duplicate validation can repoint this row at an existing file identity
        # (hash, URL and name). For the exact protected first object the identity is
        # verified instead and no retargeting is allowed; every other File keeps native
        # behaviour.
        from clinic_photos.publication import writer

        if writer.verify_exclusive_first_object(self):
            return None
        native = getattr(super(), "validate_duplicate_entry", None)
        if native is None:
            return None
        return native(*args, **kwargs)

    def write_file(self):
        require_content_write(self, "write_file")
        try:
            result = super().write_file()
        except BaseException:
            end_content_write(self, "write_file", failed=True)
            raise
        end_content_write(self, "write_file")
        return result

    def save_file_on_filesystem(self):
        require_content_write(self, "save_file_on_filesystem")
        try:
            result = super().save_file_on_filesystem()
        except BaseException:
            end_content_write(self, "save_file_on_filesystem", failed=True)
            raise
        end_content_write(self, "save_file_on_filesystem")
        return result

    def handle_is_private_changed(self):
        # Actual private/public move and alias update: native signature has no
        # argument; privacy is already inferred by ``set_is_private``.
        require_private_transition(self)
        return super().handle_is_private_changed()

    def on_rollback(self):
        # Native compensation deletes, rewrites or moves bytes using mutable File
        # fields. Protected, reserved, poisoned and unclassifiable destinations keep
        # their residue for every user instead. This never raises, so the remaining
        # rollback callbacks (including this app's lock releases) still run.
        if require_file_rollback(self):
            return super().on_rollback()
        return None

    def _delete_file_on_disk(self):
        # Direct native deletion route, including compensation dispatched from native
        # helpers: protected bytes are refused for every user, Administrator included.
        require_file_delete_on_disk(self)
        return super()._delete_file_on_disk()

    def make_thumbnail(self, *args, **kwargs):
        require_derivative(self)
        return super().make_thumbnail(*args, **kwargs)

    def optimize_file(self):
        require_derivative(self)
        return super().optimize_file()

    def delete_file_from_filesystem(self, only_thumbnail=False):
        previous = None if self.is_new() else self.get_doc_before_save()
        require_file_delete(self, previous)
        # The actual protected deletion is inside the aborting boundary: a partially
        # applied delete that raises still aborts the execution.
        with protected_cleanup_execution(self, previous):
            return super().delete_file_from_filesystem(only_thumbnail)

    def delete_file_data_content(self, only_thumbnail=False):
        previous = None if self.is_new() else self.get_doc_before_save()
        require_file_delete(self, previous)
        with protected_cleanup_execution(self, previous):
            return super().delete_file_data_content(only_thumbnail)

    def on_trash(self):
        previous = None if self.is_new() else self.get_doc_before_save()
        require_file_delete(self, previous)
        with protected_cleanup_execution(self, previous):
            result = super().on_trash()
        # Only a completed, authorized cleanup clears the consumed first-write mark.
        confirm_pending_cleanup(self)
        return result
