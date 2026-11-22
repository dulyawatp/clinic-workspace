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
import contextvars
import sys

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

_VISIT_INSERT = contextvars.ContextVar("clinic_photos_visit_native_insert", default=None)


def _visit_save_window(file_doc):
    """Proof only; never permits reads, writes, or a native permission bypass."""
    from clinic_photos.publication import visit_file_scope

    attempt = _VISIT_INSERT.get()
    if attempt is None or not attempt[1]:
        return False
    entry = attempt[0]
    try:
        if visit_file_scope._ACTIVE.get() is not entry or entry[2] is not file_doc:
            raise visit_file_scope.FileScopeDenied("visit file window unavailable")
        visit_file_scope._check_file(entry, file_doc)
        visit_file_scope._check_insert_modes(file_doc)
        if (entry[1]._native_file is not file_doc or entry[1]._native_settled or
                not entry[4][1]):
            raise visit_file_scope.FileScopeDenied("visit file window unavailable")
        return True
    except BaseException:
        entry[1].abort_publication("visit_file_window_refused")
        raise


def _visit_post_save_window(file_doc):
    """Suppress only post-save side effects for this exact persisted visit File."""
    from clinic_photos.publication import visit_file_scope

    entry = visit_file_scope._ACTIVE.get()
    attempt = _VISIT_INSERT.get()
    if entry is None and attempt is None:
        return False
    owner_entry = entry if entry is not None else attempt[0]
    try:
        from clinic_photos.publication import visit_file_aliases, visit_native_save, writer

        if (entry is None or attempt is None or attempt[0] is not entry or
                entry[2] is not file_doc or attempt[3] is None or
                attempt[3][4][0] != getattr(file_doc, "name", None) or
                not getattr(file_doc, "name", None)):
            raise visit_file_scope.FileScopeDenied("visit post-save unavailable")
        receipt = visit_native_save._verify_named_receipt(file_doc)
        row = visit_file_aliases._locked_named_row(file_doc)
        expected = {
            "name": file_doc.name, "file_url": entry[3].key,
            "file_name": entry[3].key.rsplit("/", 1)[-1], "owner": entry[1]._actor,
            "is_private": 1, "file_size": receipt.size,
            "content_hash": writer._content_hash(file_doc.content),
            "attached_to_doctype": None, "attached_to_name": None, "attached_to_field": None,
        }
        if (any(row.get(key) != value for key, value in expected.items()) or
                _VISIT_INSERT.get() is not attempt or visit_file_scope._ACTIVE.get() is not entry):
            raise visit_file_scope.FileScopeDenied("visit post-save unavailable")
        return True
    except BaseException as exc:
        owner_entry[1].abort_publication("visit_file_post_save_refused")
        raise visit_file_scope.FileScopeDenied("visit post-save unavailable") from exc


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

    def clear_cache(self, *args, **kwargs):
        if not _visit_post_save_window(self):
            return super().clear_cache(*args, **kwargs)

    def notify_update(self, *args, **kwargs):
        if not _visit_post_save_window(self):
            return super().notify_update(*args, **kwargs)

    def before_insert(self):
        from clinic_photos.publication import visit_file_scope

        entry = visit_file_scope._ACTIVE.get()
        attempt = _VISIT_INSERT.get()
        if entry is None and attempt is not None:
            attempt[0][1].abort_publication("visit_file_window_scope_lost")
            raise visit_file_scope.FileScopeDenied("visit file window unavailable")
        if entry is not None:
            try:
                if (attempt is None or attempt[0] is not entry or
                        attempt[1] or attempt[2] or entry[2] is not self):
                    raise visit_file_scope.FileScopeDenied("visit file window unavailable")
                visit_file_scope._check_file(entry, self)
                visit_file_scope._check_insert_modes(self)
                if entry[1]._native_file is not self or entry[1]._native_settled:
                    raise visit_file_scope.FileScopeDenied("visit file window unavailable")
                attempt[2] = True
                self.check_permission("create")
                visit_file_scope._check_file(entry, self)
                visit_file_scope._check_insert_modes(self)
                if _VISIT_INSERT.get() is not attempt or visit_file_scope._ACTIVE.get() is not entry:
                    raise visit_file_scope.FileScopeDenied("visit file window unavailable")
                attempt[1] = True
                try:
                    import frappe
                    from clinic_photos.publication import visit_native_save, writer

                    _visit_save_window(self)
                    receipt = visit_native_save._create_reserved_bytes(self)
                    self.file_size = receipt.size
                    self.content_hash = writer._content_hash(self.content)
                    self.flags.new_file = True
                    frappe.db.after_rollback.add(self.on_rollback)
                finally:
                    attempt[1] = False
                return
            except BaseException:
                entry[1].abort_publication("visit_file_window_refused")
                raise
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
        from clinic_photos.publication import visit_file_scope

        scoped = visit_file_scope._before_native_insert(self, args, kwargs)
        attempt = [scoped, False, False, None] if scoped else None
        token = _VISIT_INSERT.set(attempt) if scoped else None
        try:
            try:
                result = super().insert(*args, **kwargs)
                if scoped:
                    from clinic_photos.publication import visit_native_save

                    receipt = visit_native_save._verify_post_row(self)
                else:
                    require_file_persisted_identity(self)
            except BaseException:
                if scoped:
                    visit_file_scope._native_insert_failed(self, scoped)
                raise
            if scoped:
                visit_file_scope._native_insert_persisted(self, scoped)
                try:
                    visit_file_scope._install_completed(scoped, attempt, receipt)
                except BaseException:
                    scoped[1].abort_publication("visit_completed_file_install_failed")
                    raise
            return result
        finally:
            if token is not None:
                primary = sys.exc_info()[0] is not None
                try:
                    from clinic_photos.publication import visit_native_save

                    visit_native_save._close_attempt_receipt(attempt)
                except BaseException:
                    scoped[1].abort_publication("visit_file_receipt_close_failed")
                    if not primary:
                        raise
                finally:
                    _VISIT_INSERT.reset(token)

    def validate(self):
        from clinic_photos.publication import visit_file_scope

        entry = visit_file_scope._ACTIVE.get()
        if entry is not None or _VISIT_INSERT.get() is not None:
            from clinic_photos.publication import visit_native_save

            visit_native_save._verify_pre_row(self)
            super().validate()
            return
        inserting = bool(getattr(self.flags, "in_insert", False)) or self.is_new()
        previous = None if inserting else self.get_doc_before_save()
        require_file_update(self, previous, inserting=inserting)
        super().validate()

    def db_insert(self, *args, **kwargs):
        """Veto late scoped File retargeting immediately before native insertion."""
        from clinic_photos.publication import visit_file_scope

        entry = visit_file_scope._ACTIVE.get()
        attempt = _VISIT_INSERT.get()
        if entry is not None or attempt is not None:
            from clinic_photos.access import storage
            from clinic_photos.publication import visit_file_aliases, visit_native_save, writer

            try:
                if (entry is None or attempt is None or attempt[0] is not entry or
                        entry[2] is not self or args or
                        set(kwargs) - {"ignore_if_duplicate"} or
                        kwargs.get("ignore_if_duplicate") not in (None, False)):
                    raise visit_file_scope.FileScopeDenied("visit db insert unavailable")
                receipt = visit_native_save._verify_named_receipt(self, _post=True)
                key = entry[3].key
                if (storage.canonical_private_key(key) != key or
                        getattr(self, "file_size", None) != receipt.size or
                        getattr(self, "content_hash", None) != writer._content_hash(self.content)):
                    raise visit_file_scope.FileScopeDenied("visit db insert unavailable")
                visit_file_aliases._verify_no_holders(self)
                if (visit_file_scope._ACTIVE.get() is not entry or
                        _VISIT_INSERT.get() is not attempt or
                        visit_native_save._verify_named_receipt(self, _post=True) is not receipt or
                        getattr(self, "file_size", None) != receipt.size or
                        getattr(self, "content_hash", None) != writer._content_hash(self.content)):
                    raise visit_file_scope.FileScopeDenied("visit db insert unavailable")
            except BaseException as exc:
                owner_entry = entry if entry is not None else attempt[0]
                owner_entry[1].abort_publication("visit_file_db_insert_refused")
                raise visit_file_scope.FileScopeDenied("visit db insert unavailable") from exc
        return super().db_insert(*args, **kwargs)

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
        from clinic_photos.publication import visit_transaction

        if visit_transaction._suppress_native_file_rollback(self):
            return None
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
