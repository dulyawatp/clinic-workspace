"""Bounded, filesystem-only staging for publication image bytes.

The bytes are written outside the HTTP-exposed ``public/files`` tree, into the bound
site's own private area at ``<site>/private/clinic_photo_staging``. This module never
touches the database, an HTTP route, the native ``File`` document or the publication
journal: it only turns already-validated image bytes into an immutable
:class:`StagedImage` reference and reads them back with integrity checks.

Contracts:

* the staging location is resolved through ``frappe.get_site_path("private",
  "clinic_photo_staging")`` at call time, and the resolved path must still be exactly
  ``<site>/private/clinic_photo_staging`` for the bound site. The binding is required:
  an unbound process (no ``frappe.local.site``) refuses rather than staging into
  whatever site happens to resolve;
* an absolute result is used as given (normalised, never symlink-resolved); a relative
  result — Frappe's HTTP default is ``./<site>/private/clinic_photo_staging`` — is
  anchored on the trusted process working directory and must keep exactly that
  three-part shape: a ``..`` component or any extra component refuses, so it can neither
  climb out of the anchor nor address a different depth. Both forms are then held to the
  same binding and confinement checks;
* only the framework-provided site root is canonicalised once. The ``private`` and
  ``clinic_photo_staging`` components below it are opened directory-relative with
  ``O_NOFOLLOW``, so a symlinked private or staging directory is refused, never
  followed, and the staging path itself is never canonicalised into some other place.
  A site root that is itself reached through a symlink is refused, so canonicalising it
  can never open a directory other than the bound one;
* the host must provide ``O_NOFOLLOW``, ``O_DIRECTORY``, ``O_NONBLOCK`` and
  ``O_CLOEXEC``; if any is missing the module refuses instead of opening with degraded
  flags;
* the file name is a server-generated random basename (32 hex characters plus an
  extension derived from the inspected format). The returned ``key`` is that basename
  only: it is never a path, a URL or an HTTP-exposed name;
* creation is exclusive (``O_CREAT|O_EXCL``) and no-follow, with mode ``0600`` inside a
  ``0700`` directory. An existing name refuses; nothing is overwritten, truncated,
  retried or unlinked, so any failure leaves its residue in place as evidence;
* the written object is verified by read-back (same inode, same length, same bytes)
  before the file and the directory are fsynced. The private parent is fsynced on every
  create path, even when this process lost the ``mkdir`` race: a peer-created directory
  entry may be unsynced, so it is only durable once this process has synced the parent
  itself — which happens before any metadata can be committed;
* reads revalidate the recorded metadata, re-open the object no-follow and non-blocking
  inside the bound staging directory, require a single-link regular file owned by the
  effective user without group/other permissions, read a bounded ``size`` plus one
  trailing byte, and authenticate the content by size, SHA-256 and the validated image
  format; a FIFO or device at the key is refused without ever blocking;
* arbitrary keys, traversal, an absolute path, a mismatching site, a symlink, a hard
  link, a loosened mode, a missing object and corrupted, truncated or appended bytes
  all refuse with a fixed message; the payload is never echoed.

Image validation is the existing bounded inspection in ``publication.validation``
(JPEG/PNG only, at most 8 MiB, axis/pixel bounds, native header integrity). The magic
bytes are additionally checked here so the recorded format cannot be supplied by a
caller.

Not proven here: installed Frappe site resolution, transactional/DB behaviour,
power-loss durability, multi-host or network filesystem semantics, backup/restore,
retention/purge, EXIF handling or any HTTP exposure. The relative-path anchor trusts the
process working directory to be the bench ``sites`` directory; a concurrent ``chdir`` in
the same process, or a working directory that is itself reached through a symlink, is not
detected. Resolution happens once per call and every open below it is directory-relative,
so a later ``chdir`` cannot redirect an already resolved call. This module does not
implement publication; it is a bounded seam for the integrating caller.
"""

import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass

from clinic_photos.publication import validation
from clinic_photos.publication.visit_annotation_intent import VisitAnnotationBinding, VisitAnnotationIntent

STAGING_DIRECTORY = "clinic_photo_staging"
PRIVATE_DIRECTORY = "private"

FILE_MODE = 0o600
DIRECTORY_MODE = 0o700
READ_CHUNK = 1024 * 1024
KEY_HEX_CHARS = 32

SAFE_STAGING_REFUSED = "Photo image cannot be staged"
SAFE_STAGED_REFUSED = "Staged photo image cannot be read"

_EXTENSIONS = {"JPEG": "jpg", "PNG": "png"}
_FORMATS_BY_EXTENSION = {extension: form for form, extension in _EXTENSIONS.items()}
_KEY_PATTERN = re.compile(r"[0-9a-f]{32}\.(?:jpg|png)")
_ANNOTATION_KEY_PATTERN = re.compile(r"[0-9a-f]{32}\.json")
VISIT_ANNOTATION_JSON = "VISIT_ANNOTATION_JSON"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

_OPEN_DIRECTORY = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CREATE_EXCLUSIVE = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_OPEN_FILE = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
)

# Without these the confinement above silently degrades, so their absence refuses.
_REQUIRED_DIRECTORY_FLAGS = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
_REQUIRED_FILE_FLAGS = ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC")

__all__ = [
    "StagedImage",
    "StagedVisitAnnotation",
    "StagedImageRefused",
    "StagingError",
    "StagingRefused",
    "read_staged_image",
    "read_staged_visit_annotation",
    "stage_image",
    "stage_visit_annotation",
    "validate_staged_metadata",
]


class StagingError(Exception):
    """Base of the fixed-message staging refusals."""


class StagingRefused(StagingError):
    """The image cannot be staged; no caller-supplied detail is disclosed."""


class StagedImageRefused(StagingError):
    """The staged metadata or object is not usable; nothing is disclosed."""


class _Refusal(Exception):
    """Internal fixed-message denial; converted at the public boundary."""


def _refuse() -> None:
    raise _Refusal()


# ---------------------------------------------------------------------------
# Immutable staged reference
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StagedImage:
    """Immutable reference to one bounded staging object.

    ``key`` is the random basename inside the bound site's staging directory. Instances
    can only be constructed with metadata that names such an object, so a traversal,
    URL, absolute path or format/extension mismatch refuses at construction.
    """

    key: str
    sha256: str
    size: int
    format: str

    def __post_init__(self) -> None:
        validate_staged_metadata(self.key, self.sha256, self.size, self.format)


@dataclass(frozen=True, slots=True)
class StagedVisitAnnotation:
    key: str
    sha256: str
    size: int
    format: str

    def __post_init__(self) -> None:
        if (not isinstance(self.key, str) or _ANNOTATION_KEY_PATTERN.fullmatch(self.key) is None
                or not isinstance(self.sha256, str) or _SHA256_PATTERN.fullmatch(self.sha256) is None
                or type(self.size) is not int or not 0 < self.size <= validation.MAX_ANNOTATION_BYTES
                or self.format != VISIT_ANNOTATION_JSON):
            raise StagedImageRefused(SAFE_STAGED_REFUSED)


def _validated_visit_annotation(data: bytes) -> None:
    if type(data) is not bytes or not 0 < len(data) <= validation.MAX_ANNOTATION_BYTES:
        _refuse()
    try:
        text = data.decode("utf-8")
        parsed = json.loads(text)
        if validation.canonical_visit_annotation(parsed) != text:
            _refuse()
    except (UnicodeError, ValueError, RecursionError, validation.PublicationValidationError):
        raise _Refusal() from None


def validate_staged_metadata(key, sha256, size, image_format) -> None:
    """Refuse metadata that cannot name a staging object; used by construction."""
    try:
        _check_metadata(key, sha256, size, image_format)
    except _Refusal:
        raise StagedImageRefused(SAFE_STAGED_REFUSED) from None


def _check_metadata(key, sha256, size, image_format) -> None:
    if not isinstance(key, str) or _KEY_PATTERN.fullmatch(key) is None:
        _refuse()
    if not isinstance(sha256, str) or _SHA256_PATTERN.fullmatch(sha256) is None:
        _refuse()
    if isinstance(size, bool) or not isinstance(size, int):
        _refuse()
    if size <= 0 or size > validation.MAX_IMAGE_BYTES:
        _refuse()
    if not isinstance(image_format, str) or image_format not in _EXTENSIONS:
        _refuse()
    if _FORMATS_BY_EXTENSION.get(key.rsplit(".", 1)[-1]) != image_format:
        _refuse()


# ---------------------------------------------------------------------------
# Image validation (existing bounded seams) and format evidence
# ---------------------------------------------------------------------------
def _format_of(data: bytes) -> str:
    """Format from the validated header bytes; never caller-supplied."""
    if data.startswith(_PNG_MAGIC):
        return "PNG"
    if data.startswith(_JPEG_MAGIC):
        return "JPEG"
    _refuse()


def _validated_image(image_bytes):
    try:
        data = validation.validate_image_bytes(image_bytes)
    except validation.PublicationValidationError:
        raise _Refusal() from None
    # ImageInspectionUnavailable is not a client denial; it propagates unchanged.
    return data, _format_of(data)


# ---------------------------------------------------------------------------
# Confined site resolution and directory handles
# ---------------------------------------------------------------------------
def _require_flags(*names: str) -> None:
    """Fail closed when the host cannot supply a required ``open(2)`` flag."""
    for name in names:
        if not getattr(os, name, 0):
            _refuse()


def _absolute_staging_path(path: str) -> str:
    """Absolute form of the ``get_site_path`` result, without resolving symlinks.

    Frappe's HTTP default can return a path relative to the process working directory,
    which for a bench is its ``sites`` directory: ``./<site>/private/...``. A relative
    result is anchored on that trusted working directory and must keep the exact
    ``<site>/private/clinic_photo_staging`` shape, so it can neither climb out with
    ``..`` nor point at a different depth; nothing is canonicalised here.
    """
    components = [part for part in path.split(os.sep) if part not in ("", os.curdir)]
    if os.path.isabs(path):
        return os.path.normpath(path)
    if len(components) != 3 or os.pardir in components:
        _refuse()
    try:
        anchor = os.getcwd()
    except OSError:
        raise _Refusal() from None
    if not os.path.isabs(anchor):
        _refuse()
    return os.path.normpath(os.path.join(anchor, *components))


def _require_bound_site_root() -> str:
    """Canonical site root for the bound site; ``private`` stays unresolved below it.

    The bound site is required: an unbound process has no site to confine to and refuses.
    A site root reached through a symlink is refused as well, because canonicalising it
    would open a directory other than the one the binding names.
    """
    import frappe

    try:
        path = frappe.get_site_path(PRIVATE_DIRECTORY, STAGING_DIRECTORY)
    except Exception:
        raise _Refusal() from None
    if not isinstance(path, str) or not path:
        _refuse()

    normalized = _absolute_staging_path(path)
    if os.path.basename(normalized) != STAGING_DIRECTORY:
        _refuse()
    private_path = os.path.dirname(normalized)
    if os.path.basename(private_path) != PRIVATE_DIRECTORY:
        _refuse()
    site_root = os.path.dirname(private_path)
    if not site_root or site_root == os.sep:
        _refuse()

    bound_site = getattr(getattr(frappe, "local", None), "site", None)
    if not isinstance(bound_site, str) or not bound_site:
        _refuse()
    if os.path.basename(bound_site) != bound_site or bound_site in (os.curdir, os.pardir):
        _refuse()
    if os.path.basename(site_root) != bound_site:
        _refuse()

    try:
        real_root = os.path.realpath(site_root)
    except OSError:
        raise _Refusal() from None
    if not real_root or not os.path.isdir(real_root):
        _refuse()
    if real_root != os.path.normpath(site_root):
        _refuse()  # a symlinked site root is never followed
    if os.path.basename(real_root) != bound_site:
        _refuse()
    return real_root


def _open_staging_directory(*, create: bool, expected_site=None) -> int:
    """Directory descriptor for ``<site>/private/clinic_photo_staging``.

    The site root is opened first, then ``private`` (which must already exist) and the
    staging directory itself are opened relative to their parent with ``O_NOFOLLOW``.
    The staging directory is created with mode ``0700`` when ``create`` is set, and the
    private parent is fsynced on that path whether or not this process won the ``mkdir``
    race: an entry left unsynced by a peer must still be durable before metadata is
    committed. An existing directory must already be owned by the effective user with no
    group/other permission. If the host lacks the required open flags, nothing is opened.
    """
    site_root = _require_bound_site_root()
    if expected_site is not None and os.path.basename(site_root) != expected_site:
        _refuse()
    _require_flags(*_REQUIRED_DIRECTORY_FLAGS, *_REQUIRED_FILE_FLAGS)

    try:
        site_descriptor = os.open(site_root, _OPEN_DIRECTORY)
    except OSError:
        raise _Refusal() from None
    try:
        try:
            private_descriptor = os.open(
                PRIVATE_DIRECTORY, _OPEN_DIRECTORY, dir_fd=site_descriptor
            )
        except OSError:
            raise _Refusal() from None
    finally:
        _close_quietly(site_descriptor)

    try:
        if create:
            try:
                os.mkdir(STAGING_DIRECTORY, DIRECTORY_MODE, dir_fd=private_descriptor)
            except FileExistsError:
                pass
            except OSError:
                raise _Refusal() from None
            # Unconditional on purpose: a peer may have won ``mkdir`` and died before
            # syncing its parent, so the entry is only known durable once this process
            # has synced the private parent itself. Metadata must never be committed for
            # objects inside an unsynced directory entry.
            try:
                _fsync_directory(private_descriptor)
            except OSError:
                raise _Refusal() from None
        try:
            staging_descriptor = os.open(
                STAGING_DIRECTORY, _OPEN_DIRECTORY, dir_fd=private_descriptor
            )
        except OSError:
            raise _Refusal() from None
    finally:
        _close_quietly(private_descriptor)

    try:
        info = os.fstat(staging_descriptor)
        if not stat.S_ISDIR(info.st_mode):
            _refuse()
        if info.st_uid != os.geteuid():
            _refuse()
        if stat.S_IMODE(info.st_mode) & 0o077:
            _refuse()
    except BaseException:
        _close_quietly(staging_descriptor)
        raise
    return staging_descriptor


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Bounded IO and object identity
# ---------------------------------------------------------------------------
def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _read_bounded(descriptor: int, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = os.read(descriptor, min(READ_CHUNK, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _fsync_file(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _require_regular_private_file(info) -> None:
    """Identity of an object this module created: regular, single-link, ours, private."""
    if not stat.S_ISREG(info.st_mode):
        _refuse()
    if info.st_nlink != 1:
        _refuse()
    if info.st_uid != os.geteuid():
        _refuse()
    if stat.S_IMODE(info.st_mode) & 0o077:
        _refuse()


def _require_staged_object(info, expected_size: int) -> None:
    _require_regular_private_file(info)
    if int(info.st_size) != int(expected_size):
        _refuse()


def _read_verified(
    directory_descriptor: int,
    key: str,
    expected_sha256: str,
    expected_size: int,
    expected_format: str,
    identity=None,
) -> bytes:
    """Read one bounded object and authenticate size, hash and format."""
    try:
        descriptor = os.open(key, _OPEN_FILE, dir_fd=directory_descriptor)
    except OSError:
        raise _Refusal() from None
    try:
        info = os.fstat(descriptor)
        _require_staged_object(info, expected_size)
        if identity is not None and (info.st_dev, info.st_ino) != identity:
            _refuse()
        data = _read_bounded(descriptor, expected_size)
        if os.read(descriptor, 1):
            _refuse()  # nothing may follow the recorded size
    finally:
        try:
            os.close(descriptor)
        except OSError:
            raise _Refusal() from None

    if len(data) != expected_size:
        _refuse()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        _refuse()
    if expected_format == VISIT_ANNOTATION_JSON:
        _validated_visit_annotation(data)
    else:
        try:
            validation.validate_image_bytes(data)
        except validation.PublicationValidationError:
            raise _Refusal() from None
        if _format_of(data) != expected_format:
            _refuse()
    return data


# ---------------------------------------------------------------------------
# Public seams
# ---------------------------------------------------------------------------
def stage_image(image_bytes) -> StagedImage:
    """Stage validated image bytes once and return their immutable reference.

    Bounded input is validated first; nothing is written for a refused payload. A
    failure during or after the write leaves the residue in place: there is no unlink,
    truncate, overwrite or retry.
    """
    try:
        data, image_format = _validated_image(image_bytes)
        return _stage(data, image_format)
    except _Refusal:
        raise StagingRefused(SAFE_STAGING_REFUSED) from None
    except OSError:
        raise StagingRefused(SAFE_STAGING_REFUSED) from None


def _stage(data: bytes, image_format: str, *, expected_site=None) -> StagedImage | StagedVisitAnnotation:
    extension = "json" if image_format == VISIT_ANNOTATION_JSON else _EXTENSIONS[image_format]
    key = f"{secrets.token_hex(KEY_HEX_CHARS // 2)}.{extension}"
    digest = hashlib.sha256(data).hexdigest()
    size = len(data)

    if expected_site is None:
        directory_descriptor = _open_staging_directory(create=True)
    else:
        directory_descriptor = _open_staging_directory(create=True, expected_site=expected_site)
    try:
        try:
            descriptor = os.open(
                key, _CREATE_EXCLUSIVE, FILE_MODE, dir_fd=directory_descriptor
            )
        except FileExistsError:
            _refuse()  # never overwrite, never retry under another name
        except OSError:
            _refuse()

        identity = None
        try:
            info = os.fstat(descriptor)
            _require_regular_private_file(info)
            if int(info.st_size) != 0:
                _refuse()
            _write_all(descriptor, data)
            info = os.fstat(descriptor)
            if int(info.st_size) != size:
                _refuse()
            _require_regular_private_file(info)
            identity = (info.st_dev, info.st_ino)
            _fsync_file(descriptor)
            info = os.fstat(descriptor)
            if int(info.st_size) != size:
                _refuse()
            _require_regular_private_file(info)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                raise _Refusal() from None

        # Verify the actual object, not an unchecked pathname.
        readback = _read_verified(
            directory_descriptor,
            key,
            digest,
            size,
            image_format,
            identity=identity,
        )
        if readback != data:
            _refuse()
        _fsync_directory(directory_descriptor)
    finally:
        _close_quietly(directory_descriptor)

    reference = StagedVisitAnnotation if image_format == VISIT_ANNOTATION_JSON else StagedImage
    return reference(key=key, sha256=digest, size=size, format=image_format)


def stage_visit_annotation(intent: VisitAnnotationIntent) -> StagedVisitAnnotation:
    try:
        if type(intent) is not VisitAnnotationIntent:
            _refuse()
        if type(intent.binding) is not VisitAnnotationBinding or type(intent.binding.site) is not str or not intent.binding.site:
            _refuse()
        text = intent.canonical_visit_annotation
        if type(text) is not str or len(text) > validation.MAX_ANNOTATION_BYTES:
            _refuse()
        try:
            data = text.encode("utf-8")
        except UnicodeError:
            _refuse()
        _validated_visit_annotation(data)
        return _stage(data, VISIT_ANNOTATION_JSON, expected_site=intent.binding.site)
    except (_Refusal, OSError):
        raise StagingRefused("Visit annotation cannot be staged") from None


def read_staged_visit_annotation(staged: StagedVisitAnnotation) -> bytes:
    if type(staged) is not StagedVisitAnnotation:
        raise StagedImageRefused("Staged visit annotation cannot be read")
    try:
        staged.__post_init__()
        directory_descriptor = _open_staging_directory(create=False)
        try:
            return _read_verified(directory_descriptor, staged.key, staged.sha256,
                                  staged.size, staged.format)
        finally:
            _close_quietly(directory_descriptor)
    except (_Refusal, OSError, StagedImageRefused):
        raise StagedImageRefused("Staged visit annotation cannot be read") from None


def read_staged_image(staged) -> bytes:
    """Return the exact staged bytes after authenticating metadata and content."""
    if not isinstance(staged, StagedImage):
        raise StagedImageRefused(SAFE_STAGED_REFUSED)

    try:
        _check_metadata(staged.key, staged.sha256, staged.size, staged.format)
        directory_descriptor = _open_staging_directory(create=False)
        try:
            return _read_verified(
                directory_descriptor,
                staged.key,
                staged.sha256,
                staged.size,
                staged.format,
            )
        finally:
            _close_quietly(directory_descriptor)
    except _Refusal:
        raise StagedImageRefused(SAFE_STAGED_REFUSED) from None
    except OSError:
        raise StagedImageRefused(SAFE_STAGED_REFUSED) from None
