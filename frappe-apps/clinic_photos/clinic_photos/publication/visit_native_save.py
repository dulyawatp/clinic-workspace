"""Private visit-native validation and reserved-byte creation.

Only the scoped File hook uses this module. It does not itself register a
callback or authorize publication; no coordinator dispatch is wired.
"""

import contextvars
import hashlib
import os
import sys
from dataclasses import dataclass

from clinic_photos.publication import visit_file_scope

_IN_PROGRESS = contextvars.ContextVar("visit_reserved_bytes_in_progress", default=None)


def _verify_pre_row(file_doc):
    """Prove the exact reserved destination has no File holder before db_insert."""
    return _verify_row(file_doc, post=False)


def _verify_post_row(file_doc):
    """Prove fresh persisted identity and a singleton canonical destination."""
    return _verify_row(file_doc, post=True)


def _verify_named_receipt(file_doc, *, _post=False):
    """Check one captured attempt and bind its assigned native name once."""
    from clinic_photos.access import file_mixin

    entry = visit_file_scope._ACTIVE.get()
    try:
        attempt = file_mixin._VISIT_INSERT.get()
        if (entry is None or attempt is None or len(attempt) != 4 or
                attempt[0] is not entry or entry[2] is not file_doc or
                attempt[3] is None):
            raise visit_file_scope.FileScopeDenied("visit named receipt unavailable")
        captured_entry, captured_file, reservation, receipt, name_slot = attempt[3]
        guard = entry[1]
        guard._check()
        name = getattr(file_doc, "name", None)
        data = getattr(file_doc, "content", None)
        if (captured_entry is not entry or captured_file is not file_doc or
                reservation is not entry[3] or guard._phase != "publish" or
                guard._native_file is not file_doc or guard._native_settled or
                not entry[4][0] or not entry[4][1] or
                bool(getattr(getattr(file_doc, "flags", None), "in_insert", False)) == _post or
                type(name) is not str or not name or
                getattr(file_doc, "file_url", None) != reservation.key or
                getattr(file_doc, "file_name", None) != reservation.key.rsplit("/", 1)[-1] or
                getattr(file_doc, "owner", None) != guard._actor or
                getattr(file_doc, "is_private", None) != 1 or
                any(getattr(file_doc, field, None) not in (None, "") for field in
                    ("attached_to_doctype", "attached_to_name", "attached_to_field")) or
                type(data) is not bytes or len(data) != reservation.size or
                hashlib.sha256(data).hexdigest() != reservation.digest or
                type(receipt) is not _CreatedBytes or
                receipt.size != reservation.size or receipt.sha256 != reservation.digest):
            raise visit_file_scope.FileScopeDenied("visit named receipt unavailable")
        if name_slot[0] is None and not _post:
            name_slot[0] = name
        elif name_slot[0] != name:
            raise visit_file_scope.FileScopeDenied("visit named receipt unavailable")
        return receipt
    except BaseException:
        if entry is not None:
            entry[1].abort_publication("visit_named_receipt_refused")
        raise


def _verify_row(file_doc, *, post):
    from clinic_photos.access import file_mixin, storage
    from clinic_photos.publication import visit_file_aliases, writer

    entry = visit_file_scope._ACTIVE.get()
    try:
        receipt = _verify_named_receipt(file_doc, _post=post)
        reservation = entry[3]
        key = reservation.key
        if storage.canonical_private_key(key) != key:
            raise visit_file_scope.FileScopeDenied("visit row proof unavailable")
        expected_hash = writer._content_hash(file_doc.content)
        if (getattr(file_doc, "file_size", None) != receipt.size or
                getattr(file_doc, "content_hash", None) != expected_hash):
            raise visit_file_scope.FileScopeDenied("visit row proof unavailable")
        if post:
            visit_file_aliases._verify_single_holder(file_doc)
            row = visit_file_aliases._locked_named_row(file_doc)
            expected = {
                "name": file_doc.name, "file_url": key,
                "file_name": file_doc.file_name, "owner": entry[1]._actor,
                "is_private": 1, "file_size": receipt.size, "content_hash": expected_hash,
                "attached_to_doctype": None, "attached_to_name": None,
                "attached_to_field": None,
            }
            if (not isinstance(row, dict) or
                    any(k not in row or row[k] != v for k, v in expected.items())):
                raise visit_file_scope.FileScopeDenied("visit row proof unavailable")
            _verify_persisted_bytes(entry, receipt)
        else:
            visit_file_aliases._verify_no_holders(file_doc)
        if (visit_file_scope._ACTIVE.get() is not entry or
                file_mixin._VISIT_INSERT.get()[0] is not entry or
                _verify_named_receipt(file_doc, _post=post) is not receipt):
            raise visit_file_scope.FileScopeDenied("visit row proof unavailable")
        return receipt
    except BaseException as exc:
        if entry is not None:
            entry[1].abort_publication("visit_row_proof_refused")
        raise visit_file_scope.FileScopeDenied("visit row proof unavailable") from exc


def _verify_persisted_bytes(entry, receipt):
    """Recheck exact on-disk bytes without exposing them or following links."""
    from clinic_photos.publication import writer

    descriptors = []
    try:
        entry[1]._check()
        if visit_file_scope._ACTIVE.get() is not entry:
            raise visit_file_scope.FileScopeDenied("visit byte proof unavailable")
        owner = entry[1]._exclusion_owner
        name = writer._relative_basename(entry[3].key)
        directory = receipt.directory_fd
        descriptor = receipt.file_fd
        opened = os.fstat(descriptor)
        writer._require_created_object(opened)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
                receipt.device, receipt.inode, receipt.size):
            raise visit_file_scope.FileScopeDenied("visit byte proof unavailable")
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = writer._read_all(descriptor, receipt.size)
        if len(data) != receipt.size or hashlib.sha256(data).hexdigest() != receipt.sha256:
            raise visit_file_scope.FileScopeDenied("visit byte proof unavailable")
        after = os.fstat(descriptor)
        writer._require_created_object(after)
        if (after.st_dev, after.st_ino, after.st_size) != (
                receipt.device, receipt.inode, receipt.size):
            raise visit_file_scope.FileScopeDenied("visit byte proof unavailable")
        fresh = writer._open_visit_files_directory(owner)
        descriptors.append(fresh)
        pinned, rooted = os.fstat(directory), os.fstat(fresh)
        if (pinned.st_dev, pinned.st_ino) != (rooted.st_dev, rooted.st_ino):
            raise visit_file_scope.FileScopeDenied("visit byte proof unavailable")
        writer._require_matching_entry(directory, name, receipt.device, receipt.inode, receipt.size)
        entry[1]._check()
        if visit_file_scope._ACTIVE.get() is not entry:
            raise visit_file_scope.FileScopeDenied("visit byte proof unavailable")
    finally:
        primary = sys.exc_info()[0] is not None
        failed = False
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                failed = True
        if failed:
            entry[1].abort_publication("visit_byte_proof_close_failed")
            if not primary:
                raise visit_file_scope.FileScopeDenied("visit byte proof unavailable")


def _validated_content(file_doc):
    from clinic_photos.access import file_mixin

    entry = visit_file_scope._ACTIVE.get()
    try:
        if (entry is None or not file_mixin._visit_save_window(file_doc) or
                entry[4][0]):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        data = visit_file_scope._check_file(entry, file_doc)
        # The reservation is already canonical. Native URL decoding must not
        # transform it into a different destination.
        from urllib.parse import unquote

        if unquote(file_doc.file_url) != file_doc.file_url:
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        for name in ("set_folder_name", "set_is_private", "set_file_name",
                     "validate_attachment_limit", "set_file_type",
                     "validate_file_extension", "validate_private_file_access"):
            getattr(file_doc, name)()
            if (visit_file_scope._ACTIVE.get() is not entry or
                    not file_mixin._visit_save_window(file_doc)):
                raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        if (getattr(file_doc, "is_folder", None) not in (0, False) or
                getattr(file_doc, "is_remote_file", None) not in (0, False)):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")

        from frappe.utils import get_hook_method

        if get_hook_method("before_write_file") or get_hook_method("write_file"):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        file_doc._content = data
        if file_doc.check_max_file_size() != len(data):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        if (visit_file_scope._ACTIVE.get() is not entry or
                not file_mixin._visit_save_window(file_doc) or
                visit_file_scope._check_file(entry, file_doc) != data):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        return data
    except BaseException:
        if entry is not None:
            entry[1].abort_publication("visit_native_save_refused")
        raise


@dataclass(frozen=True)
class _CreatedBytes:
    device: int
    inode: int
    size: int
    sha256: str
    directory_fd: int
    file_fd: int


def _close_attempt_receipt(attempt):
    """Release transferred fds once, attempting both closes on failure."""
    if attempt is None or attempt[3] is None:
        return
    receipt = attempt[3][3]
    attempt[3] = None
    failed = False
    for fd in (receipt.file_fd, receipt.directory_fd):
        try:
            os.close(fd)
        except OSError:
            failed = True
    if failed:
        raise visit_file_scope.FileScopeDenied("visit native receipt close failed")


def _create_reserved_bytes(file_doc):
    """One exclusive attempt in the exact live native window; no cleanup."""
    from clinic_photos.access import file_mixin
    from clinic_photos.publication import visit_file_aliases, writer

    entry = visit_file_scope._ACTIVE.get()
    directory = created = None
    token = None
    try:
        if (entry is None or not file_mixin._visit_save_window(file_doc) or
                _IN_PROGRESS.get() is not None):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        token = _IN_PROGRESS.set(entry)
        data = _validated_content(file_doc)
        file_doc.validate_file_path()
        if (visit_file_scope._ACTIVE.get() is not entry or
                not file_mixin._visit_save_window(file_doc)):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        scope, guard, _, reservation, _ = entry
        guard._acquire_visit_file_lock(scope)
        visit_file_aliases._verify_no_holders(file_doc)
        if (visit_file_scope._ACTIVE.get() is not entry or
                not file_mixin._visit_save_window(file_doc)):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        # Claim before any filesystem side effect; retries are never permitted.
        data = scope._claim(file_doc, content=data)
        name = writer._relative_basename(reservation.key)
        owner = guard._exclusion_owner
        directory = writer._open_visit_files_directory(owner)

        def check_root():
            if (visit_file_scope._ACTIVE.get() is not entry or
                    not file_mixin._visit_save_window(file_doc)):
                raise visit_file_scope.FileScopeDenied("visit native save unavailable")
            fresh = writer._open_visit_files_directory(owner)
            try:
                pinned, current = os.fstat(directory), os.fstat(fresh)
                if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
                    raise visit_file_scope.FileScopeDenied("visit native save unavailable")
            finally:
                primary = sys.exc_info()[0] is not None
                try:
                    os.close(fresh)
                except OSError:
                    guard.abort_publication("visit_native_save_close_failed")
                    if not primary:
                        raise

        check_root()
        flags = (writer._CREATE_FLAGS & ~os.O_ACCMODE) | os.O_RDWR
        created = os.open(name, flags, writer.FILE_MODE, dir_fd=directory)
        writer._require_created_object(os.fstat(created))
        writer._write_all(created, data)
        writer._fsync_file(created)
        info = os.fstat(created)
        writer._require_created_object(info)
        if info.st_size != len(data):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        os.lseek(created, 0, os.SEEK_SET)
        actual = writer._read_all(created, len(data))
        if actual != data or hashlib.sha256(actual).hexdigest() != reservation.digest:
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        writer._require_matching_entry(directory, name, info.st_dev, info.st_ino, len(data))
        writer._fsync_directory(directory)
        check_root()
        writer._require_matching_entry(directory, name, info.st_dev, info.st_ino, len(data))
        final = os.fstat(created)
        writer._require_created_object(final)
        if (final.st_dev, final.st_ino, final.st_size) != (info.st_dev, info.st_ino, len(data)):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        os.lseek(created, 0, os.SEEK_SET)
        if hashlib.sha256(writer._read_all(created, len(data))).hexdigest() != reservation.digest:
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        final = os.fstat(created)
        writer._require_created_object(final)
        if (final.st_dev, final.st_ino, final.st_size) != (info.st_dev, info.st_ino, len(data)):
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
        check_root()
        writer._require_matching_entry(directory, name, info.st_dev, info.st_ino, len(data))
        receipt = _CreatedBytes(info.st_dev, info.st_ino, len(data), reservation.digest,
                                directory, created)
        attempt = file_mixin._VISIT_INSERT.get()
        if (visit_file_scope._ACTIVE.get() is not entry or attempt is None or
                len(attempt) != 4 or attempt[0] is not entry or attempt[3] is not None or
                entry[2] is not file_doc or entry[3] is not reservation or
                not file_mixin._visit_save_window(file_doc)):
            raise visit_file_scope.FileScopeDenied("visit native receipt unavailable")
        visit_file_scope._check_file(entry, file_doc)
        attempt[3] = (entry, file_doc, reservation, receipt, [None])
        directory = created = None
        return receipt
    except BaseException:
        if entry is not None:
            entry[1].abort_publication("visit_native_save_bytes_refused")
        raise
    finally:
        primary = sys.exc_info()[0] is not None
        close_failed = False
        for fd in (created, directory):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    close_failed = True
        if close_failed:
            if entry is not None:
                entry[1].abort_publication("visit_native_save_close_failed")
        if token is not None:
            _IN_PROGRESS.reset(token)
        if close_failed and not primary:
            raise visit_file_scope.FileScopeDenied("visit native save unavailable")
