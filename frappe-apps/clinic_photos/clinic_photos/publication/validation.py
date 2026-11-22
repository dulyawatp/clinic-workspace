"""Bounded validation for the durable photo-publication request.

The limits are the approved development defaults in
``docs/superpowers/specs/2026-11-08-frappe-photo-publication-entry-design.md``
("Concrete development contracts"). They are backend bounds, not clinical claims.

Image transport is decoded once and inspected through the installed native image
library. The decoded bytes are returned unchanged: nothing is re-encoded, resized or
normalised here, so the stored payload has exactly one representation.
"""

import base64
import binascii
import io
import json
import math
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keeps this module inert and importable without the contracts module
    from .contracts import SaveRequest

MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_ANNOTATION_BYTES = 256 * 1024
MAX_JSON_CONTAINERS = 8
MAX_SHAPES = 512
MAX_FREEHAND_POINTS = 1024
MAX_DOCUMENT_POINTS = 8192
MAX_TEXT_CHARS = 1024
MIN_REQUEST_KEY = 16
MAX_REQUEST_KEY = 128
MAX_REFERENCE_CHARS = 140
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_AXIS = 8192
MAX_IMAGE_PIXELS = 40_000_000

SCHEMA_VERSION = 1
COORDINATE_SPACE = "normalized"
SHAPE_TYPES = frozenset({"arrow", "ellipse", "rectangle", "text"})

SAFE_VALIDATION = "Publication request is not a valid durable photo save"

_IMAGE_FORMATS = ("PNG", "JPEG")

_REQUEST_KEY = re.compile(
    rf"[A-Za-z0-9_-]{{{MIN_REQUEST_KEY},{MAX_REQUEST_KEY}}}"
)
_SHAPE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")

_COMMON_SHAPE_FIELDS = frozenset({"id", "type", "color", "x", "y"})
_SHAPE_FIELDS = {
    "arrow": _COMMON_SHAPE_FIELDS | {"end_x", "end_y", "stroke_width"},
    "ellipse": _COMMON_SHAPE_FIELDS | {"width", "height", "stroke_width"},
    "rectangle": _COMMON_SHAPE_FIELDS | {"width", "height", "stroke_width"},
    "text": _COMMON_SHAPE_FIELDS | {"text", "font_size"},
}


class PublicationValidationError(ValueError):
    """Fixed-message denial; payload contents are never included."""


class ImageInspectionUnavailable(RuntimeError):
    """The installed stack cannot inspect image bytes natively.

    Not a client validation failure: the caller must not treat this as a rejected
    request or fabricate an operation.
    """


def _reject() -> None:
    raise PublicationValidationError(SAFE_VALIDATION)


def check_request_size(document) -> str:
    """Public request cap: 12 MiB of UTF-8, checked before parsing the payload."""
    if not isinstance(document, str):
        _reject()
    try:
        size = len(document.encode("utf-8"))
    except UnicodeEncodeError:
        _reject()
    if size > MAX_REQUEST_BYTES:
        _reject()
    return document


def require_request_key(value) -> str:
    if not isinstance(value, str) or _REQUEST_KEY.fullmatch(value) is None:
        _reject()
    return value


def require_reference(value) -> str:
    """Document references: nonempty, no surrounding whitespace, at most 140 chars."""
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > MAX_REFERENCE_CHARS
    ):
        _reject()
    return value


def json_depth(value, _containers: int = 0) -> int:
    """Nested container count of a JSON value, saturating just past the cap.

    Scalars are depth 0 and every mapping/list counts one, including an empty one.
    The traversal stops as soon as ``MAX_JSON_CONTAINERS`` is exceeded, so cyclic or
    over-deep input is denied instead of exhausting the interpreter stack.
    """
    if _containers > MAX_JSON_CONTAINERS:
        return _containers
    if isinstance(value, Mapping):
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return _containers
    return max((json_depth(child, _containers + 1) for child in children), default=_containers + 1)


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject()
    if not math.isfinite(value):
        _reject()
    return float(value)


def _unit(value) -> float:
    number = _number(value)
    if number < 0 or number > 1:
        _reject()
    return number


def _in_open_unit_interval(value, upper) -> float:
    number = _number(value)
    if number <= 0 or number > upper:
        _reject()
    return number


def _positive(value) -> float:
    number = _number(value)
    if number <= 0:
        _reject()
    return number


def _validate_shape(shape, *, visit: bool = False) -> str:
    if not isinstance(shape, Mapping) or any(
        not isinstance(key, str) for key in shape
    ):
        _reject()

    kind = shape.get("type")
    if not isinstance(kind, str) or (
        kind not in SHAPE_TYPES and not (visit and kind == "freehand")
    ):
        _reject()
    fields = (
        {"id", "type", "color", "stroke_width", "points"}
        if kind == "freehand" else _SHAPE_FIELDS[kind]
    )
    if set(shape) != fields:
        _reject()

    shape_id = shape["id"]
    if not isinstance(shape_id, str) or _SHAPE_ID.fullmatch(shape_id) is None:
        _reject()

    color = shape["color"]
    if not isinstance(color, str) or _COLOR.fullmatch(color) is None:
        _reject()

    if kind == "freehand":
        _in_open_unit_interval(shape["stroke_width"], 0.1)
        points = shape["points"]
        if not isinstance(points, list) or not 2 <= len(points) <= MAX_FREEHAND_POINTS:
            _reject()
        for point in points:
            if not isinstance(point, Mapping) or set(point) != {"x", "y"}:
                _reject()
            _unit(point["x"])
            _unit(point["y"])
        return shape_id

    x = _unit(shape["x"])
    y = _unit(shape["y"])

    if kind == "text":
        label = shape["text"]
        if not isinstance(label, str) or not label or len(label) > MAX_TEXT_CHARS:
            _reject()
        _in_open_unit_interval(shape["font_size"], 0.25)
    elif kind == "arrow":
        _unit(shape["end_x"])
        _unit(shape["end_y"])
        _in_open_unit_interval(shape["stroke_width"], 0.1)
    else:
        width = _positive(shape["width"])
        height = _positive(shape["height"])
        _in_open_unit_interval(shape["stroke_width"], 0.1)
        if x + width > 1 or y + height > 1:
            _reject()

    return shape_id


def canonical_annotation(value) -> str:
    """Validate the legacy v1 envelope and return canonical JSON text."""
    return _canonical_annotation(value, version=SCHEMA_VERSION)


def canonical_visit_annotation(value) -> str:
    """Validate the isolated visit v2 envelope and return canonical JSON text."""
    try:
        return _canonical_annotation(value, version=2)
    except OverflowError:
        _reject()


def _canonical_annotation(value, *, version: int) -> str:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        _reject()
    if json_depth(value) > MAX_JSON_CONTAINERS:
        _reject()
    if set(value) != {"schema_version", "coordinate_space", "shapes"}:
        _reject()

    actual_version = value["schema_version"]
    if isinstance(actual_version, bool) or not isinstance(actual_version, int) or actual_version != version:
        _reject()
    if value["coordinate_space"] != COORDINATE_SPACE:
        _reject()

    shapes = value["shapes"]
    if not isinstance(shapes, list) or len(shapes) > MAX_SHAPES:
        _reject()

    seen = set()
    point_count = 0
    for shape in shapes:
        shape_id = _validate_shape(shape, visit=version == 2)
        if shape_id in seen:
            _reject()
        seen.add(shape_id)
        if shape["type"] == "freehand":
            point_count += len(shape["points"])
            if point_count > MAX_DOCUMENT_POINTS:
                _reject()

    try:
        text = json.dumps(
            dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError):
        _reject()

    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        _reject()
    if size > MAX_ANNOTATION_BYTES:
        _reject()
    return text


def check_decoded_image_size(length) -> int:
    if isinstance(length, bool) or not isinstance(length, int):
        _reject()
    if length <= 0 or length > MAX_IMAGE_BYTES:
        _reject()
    return length


def decode_image_base64(value) -> bytes:
    """Strict standard base64, canonical form, at most 8 MiB decoded."""
    if not isinstance(value, str) or not value or len(value) % 4:
        _reject()
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        _reject()
    if base64.b64encode(data).decode("ascii") != value:
        _reject()
    check_decoded_image_size(len(data))
    return data


def check_image_bounds(width, height) -> None:
    """Each axis <= 8,192 and total pixels <= 40,000,000, inclusive."""
    for value in (width, height):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            _reject()
        if value > MAX_IMAGE_AXIS:
            _reject()
    if width * height > MAX_IMAGE_PIXELS:
        _reject()


def inspect_image(data: bytes) -> None:
    """Read format and dimensions from the header; verify integrity without decoding."""
    image_dimensions(data)


def image_dimensions(data: bytes) -> tuple[int, int]:
    """Validate the exact bounds and return the actual ``(width, height)``.

    Same single native inspection as :func:`inspect_image`; the returned dimensions are
    the persisted original evidence, never a client-supplied size.
    """
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:  # pragma: no cover - installed stack has Pillow
        raise ImageInspectionUnavailable(
            "Native image inspection is unavailable"
        ) from exc

    try:
        with Image.open(io.BytesIO(data), formats=_IMAGE_FORMATS) as image:
            if image.format not in _IMAGE_FORMATS:
                _reject()
            width, height = image.size
            check_image_bounds(width, height)
            image.verify()
    except PublicationValidationError:
        raise
    except Exception:
        # Pillow reports unknown, truncated and bomb-sized files with assorted
        # exception types; every one of them is a fixed-message denial here.
        _reject()
    return width, height


def validate_image_bytes(data) -> bytes:
    """Validate exact decoded image bytes and return them unchanged."""
    if not isinstance(data, bytes):
        _reject()
    check_decoded_image_size(len(data))
    inspect_image(data)
    return data


def validate_request(payload: dict | str) -> "SaveRequest":
    """Shared public request seam: parsed object, JSON text, or its UTF-8 bytes.

    The 12-MiB request cap is applied before any JSON parsing and before the image
    transport is decoded, including for an already-parsed mapping (measured by its
    serialised size). Cycles, non-finite numbers and over-deep documents deny with
    the fixed message instead of raising a traversal error.
    """
    from .contracts import _validate_payload

    if isinstance(payload, bytes):
        if len(payload) > MAX_REQUEST_BYTES:
            _reject()
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            _reject()

    if isinstance(payload, str):
        check_request_size(payload)
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError, RecursionError):
            _reject()
        return _validate_payload(parsed)

    if not isinstance(payload, Mapping):
        _reject()

    try:
        serialized = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        _reject()
    check_request_size(serialized)
    return _validate_payload(payload)
