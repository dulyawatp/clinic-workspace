"""Public-seam tests for the bounded asynchronous publication request contract.

The limits asserted here are the approved development defaults recorded in
``docs/superpowers/specs/2026-11-08-frappe-photo-publication-entry-design.md``
("Concrete development contracts"): 12-MiB request, 256-KiB canonical annotation,
8 nested containers, 512 shapes, 1,024 text characters, 16-128 character request
keys, 140-character references, 8-MiB decoded image and 8,192/pixel-40,000,000
image bounds.

Every case runs the real validator over real PNG/JPEG bytes. The image writer is
only used by tests to materialise dimension fixtures that cannot be expressed as a
small literal; the validator itself only reads them through the installed native
image library. These tests are framework-independent and do not prove installed
Frappe, HTTP or transport behaviour.
"""

import base64
import io
import json
import unittest

from clinic_photos.publication import contracts, validation

# Known-good 1x1 PNG and 2x2 JPEG fixtures produced by the installed native writer.
PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)
JPEG_2X2_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wA"
    "ARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QA"
    "tRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2"
    "JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eX"
    "qDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2u"
    "Hi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8"
    "QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYn"
    "LRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eH"
    "l6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2N"
    "na4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDgqKKK8M/VD//Z"
)

PNG_1X1_BYTES = base64.b64decode(PNG_1X1_BASE64)


ENVELOPE = {"schema_version": 1, "coordinate_space": "normalized"}


def ann(*shapes):
    return {**ENVELOPE, "shapes": list(shapes)}


def shape(kind, **values):
    base = {"id": "shape-1", "type": kind, "color": "#1a2B3c", "x": 0.25, "y": 0.5}
    base.update(values)
    return base


def rectangle(**values):
    defaults = {"width": 0.5, "height": 0.25, "stroke_width": 0.02}
    return shape("rectangle", **{**defaults, **values})


def arrow(**values):
    defaults = {"end_x": 0.5, "end_y": 0.75, "stroke_width": 0.02}
    return shape("arrow", **{**defaults, **values})


def ellipse(**values):
    defaults = {"width": 0.5, "height": 0.25, "stroke_width": 0.02}
    return shape("ellipse", **{**defaults, **values})


def text(**values):
    defaults = {"text": "Lesion", "font_size": 0.1}
    return shape("text", **{**defaults, **values})


def canonical_size(annotation):
    return len(
        json.dumps(annotation, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def annotation_of_exact_size(target_bytes):
    """Valid annotation whose canonical UTF-8 form is exactly ``target_bytes``."""
    shapes = []
    for index in range(validation.MAX_SHAPES):
        candidate = text(id=f"s{index}", text="x")
        deficit = target_bytes - canonical_size(ann(*shapes, candidate))
        if deficit > validation.MAX_TEXT_CHARS - 1:
            candidate["text"] = "x" * validation.MAX_TEXT_CHARS
            shapes.append(candidate)
            continue
        candidate["text"] = "x" * (1 + deficit)
        return ann(*shapes, candidate)
    raise AssertionError("annotation byte target is unreachable")


def png_bytes(width, height):
    """Small real PNG of an exact size; test-only native fixture writer."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("1", (width, height)).save(buffer, format="PNG")
    return buffer.getvalue()


class ImageTransportTests(unittest.TestCase):
    def test_strict_base64_yields_the_exact_decoded_bytes(self):
        self.assertEqual(validation.decode_image_base64(PNG_1X1_BASE64), PNG_1X1_BYTES)

        jpeg = validation.decode_image_base64(JPEG_2X2_BASE64)
        self.assertTrue(jpeg.startswith(b"\xff\xd8\xff"))
        self.assertIs(validation.validate_image_bytes(jpeg), jpeg)

    def test_base64_rejects_prefixes_whitespace_and_non_canonical_forms(self):
        for value in (
            "data:image/png;base64," + PNG_1X1_BASE64,
            PNG_1X1_BASE64[:10] + "\n" + PNG_1X1_BASE64[10:],
            " " + PNG_1X1_BASE64,
            PNG_1X1_BASE64 + " ",
            PNG_1X1_BASE64[:-1],
            "AB==",
            "",
            None,
            b"bytes",
        ):
            with self.subTest(value=value):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.decode_image_base64(value)

    def test_decoded_size_cap_is_eight_mebibytes(self):
        padded = PNG_1X1_BYTES + b"\x00" * (validation.MAX_IMAGE_BYTES - len(PNG_1X1_BYTES))
        self.assertEqual(len(padded), 8 * 1024 * 1024)
        self.assertEqual(validation.validate_image_bytes(padded), padded)

        oversized = padded + b"\x00"
        self.assertEqual(len(oversized), 8 * 1024 * 1024 + 1)
        with self.assertRaises(validation.PublicationValidationError):
            validation.validate_image_bytes(oversized)

    def test_axis_and_pixel_bounds_are_inclusive_at_the_documented_cap(self):
        validation.check_image_bounds(8192, 1)
        validation.check_image_bounds(8000, 5000)
        self.assertEqual(8000 * 5000, validation.MAX_IMAGE_PIXELS)

        for width, height in ((8193, 1), (1, 8193), (8000, 5001), (0, 1), (1, 0)):
            with self.subTest(width=width, height=height):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.check_image_bounds(width, height)

        with self.assertRaises(validation.PublicationValidationError):
            validation.validate_image_bytes(png_bytes(8193, 1))
        self.assertEqual(
            validation.validate_image_bytes(png_bytes(8000, 5000)), png_bytes(8000, 5000)
        )

    def test_undecodable_or_truncated_bytes_are_rejected_without_re_encoding(self):
        for data in (
            b"not an image",
            PNG_1X1_BYTES[:20],
            b"\x89PNG\r\n\x1a\n",
            bytearray(PNG_1X1_BYTES),
            "",
            None,
        ):
            with self.subTest(data=data):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.validate_image_bytes(data)

        self.assertIs(validation.validate_image_bytes(PNG_1X1_BYTES), PNG_1X1_BYTES)


class RequestBoundTests(unittest.TestCase):
    def test_request_key_is_16_to_128_ascii_characters(self):
        for key in ("k" * 16, "request-key-0001", "aZ09_-" * 21 + "ab"):
            with self.subTest(key=key):
                self.assertEqual(validation.require_request_key(key), key)

        for key in (
            "k" * 15,
            "k" * 129,
            "request key 0001",
            "request-key-0001!",
            "é" * 16,
            "",
            None,
            16,
        ):
            with self.subTest(key=key):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.require_request_key(key)

    def test_document_references_are_bounded_and_trimmed(self):
        for reference in ("D", "PHOTO-1", "R" * 140):
            with self.subTest(reference=reference):
                self.assertEqual(validation.require_reference(reference), reference)

        for reference in ("R" * 141, " DRAFT-1", "DRAFT-1 ", "", "  ", None, 1):
            with self.subTest(reference=reference):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.require_reference(reference)

    def test_request_document_cap_is_twelve_mebibytes_utf8(self):
        cap = validation.MAX_REQUEST_BYTES
        self.assertEqual(cap, 12 * 1024 * 1024)
        validation.check_request_size("x" * cap)
        with self.assertRaises(validation.PublicationValidationError):
            validation.check_request_size("x" * (cap + 1))
        with self.assertRaises(validation.PublicationValidationError):
            validation.check_request_size("é" * (cap // 2 + 1))


class AnnotationContractTests(unittest.TestCase):
    def test_all_four_shape_variants_produce_one_canonical_form(self):
        annotation = ann(
            rectangle(id="rect"),
            arrow(id="arrow"),
            ellipse(id="ellipse"),
            text(id="label"),
        )
        expected = (
            '{"coordinate_space":"normalized","schema_version":1,"shapes":['
            '{"color":"#1a2B3c","height":0.25,"id":"rect","stroke_width":0.02,'
            '"type":"rectangle","width":0.5,"x":0.25,"y":0.5},'
            '{"color":"#1a2B3c","end_x":0.5,"end_y":0.75,"id":"arrow",'
            '"stroke_width":0.02,"type":"arrow","x":0.25,"y":0.5},'
            '{"color":"#1a2B3c","height":0.25,"id":"ellipse","stroke_width":0.02,'
            '"type":"ellipse","width":0.5,"x":0.25,"y":0.5},'
            '{"color":"#1a2B3c","font_size":0.1,"id":"label","text":"Lesion",'
            '"type":"text","x":0.25,"y":0.5}]}'
        )
        self.assertEqual(validation.canonical_annotation(annotation), expected)

    def test_envelope_and_shape_fields_are_exact(self):
        for value in (
            {"schema_version": 1, "coordinate_space": "normalized"},
            {**ENVELOPE, "shapes": [], "extra": 1},
            {"schema_version": 2, "coordinate_space": "normalized", "shapes": []},
            {"schema_version": True, "coordinate_space": "normalized", "shapes": []},
            {"schema_version": 1, "coordinate_space": "viewport", "shapes": []},
            {**ENVELOPE, "shapes": {}},
            [],
            "shapes",
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(value)

        for kind in ("freehand", "line", "path", "image", "FREEFORM"):
            with self.subTest(kind=kind):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(
                        ann(shape(kind, width=0.1, height=0.1, stroke_width=0.01))
                    )

        for extra in (
            {"html": "<b>x</b>"},
            {"src": "https://example.test/x.png"},
            {"points": [[0.1, 0.1], [0.2, 0.2]]},
            {"font_size": 0.1},
        ):
            with self.subTest(extra=extra):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(ann(rectangle(**extra)))

        for missing in ("id", "type", "color", "x", "y"):
            with self.subTest(missing=missing):
                broken = rectangle()
                broken.pop(missing)
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(ann(broken))

    def test_shape_ids_are_unique_and_bounded(self):
        for shape_id in ("a", "A0_-" + "z" * 60, "s" * 64):
            with self.subTest(shape_id=shape_id):
                self.assertEqual(
                    validation.canonical_annotation(
                        ann(rectangle(id=shape_id))
                    ),
                    json.dumps(
                        ann(rectangle(id=shape_id)),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )

        for shape_id in ("", "s" * 65, "bad id", "bad/id", "é", 1, None):
            with self.subTest(shape_id=shape_id):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(ann(rectangle(id=shape_id)))

        with self.assertRaises(validation.PublicationValidationError):
            validation.canonical_annotation(ann(rectangle(id="dup"), arrow(id="dup")))

    def test_colors_geometry_and_numeric_types_are_bounded(self):
        validation.canonical_annotation(
            ann(rectangle(x=0.0, y=0.5, width=1.0, height=0.5, stroke_width=0.1))
        )

        for color in ("#000000", "#ffffff", "#1a2B3c"):
            with self.subTest(color=color):
                validation.canonical_annotation(ann(rectangle(color=color)))
        for color in ("#00000", "#gggggg", "red", "#0000000", 0x000000, None):
            with self.subTest(color=color):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(ann(rectangle(color=color)))

        for values in (
            {"x": 1.0001},
            {"x": True},
            {"x": float("nan")},
            {"x": float("inf")},
            {"x": "0.5"},
            {"x": -0.1},
            {"y": None},
            {"width": 0.0},
            {"width": -0.1},
            {"height": 0.0},
            {"stroke_width": 0.0},
            {"stroke_width": 0.1001},
        ):
            with self.subTest(values=values):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(ann(rectangle(**values)))

        for values in (
            {"x": 0.5, "width": 0.5001},
            {"y": 0.5, "height": 0.5001},
            {"end_x": 1.0001},
            {"end_y": -0.0001},
            {"stroke_width": 0.0},
        ):
            with self.subTest(values=values):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(ann(arrow(**values)))

        validation.canonical_annotation(ann(rectangle(x=0.5, y=0.5, width=0.5, height=0.5, stroke_width=0.1)))
        validation.canonical_annotation(ann(arrow(end_x=0.0, end_y=1.0, stroke_width=0.1)))

    def test_text_shapes_are_plain_nonempty_and_bounded(self):
        for label in ("x" * 1024, " x", "x ", "  surrounding spaces  ", "é" * 1024):
            with self.subTest(label=label):
                canonical = validation.canonical_annotation(ann(text(text=label)))
                self.assertIn(json.dumps(label), canonical)

        for values in (
            {"text": ""},
            {"text": "x" * 1025},
            {"text": 1},
            {"font_size": 0.0},
            {"font_size": 0.2501},
            {"font_size": True},
        ):
            with self.subTest(values=values):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(ann(text(**values)))

    def test_annotation_and_shape_counts_are_bounded(self):
        shapes = [rectangle(id=f"s{index}") for index in range(512)]
        validation.canonical_annotation(ann(*shapes))

        with self.assertRaises(validation.PublicationValidationError):
            validation.canonical_annotation(
                ann(*shapes, rectangle(id="overflow"))
            )

        exact = annotation_of_exact_size(validation.MAX_ANNOTATION_BYTES)
        self.assertEqual(
            canonical_size(exact), validation.MAX_ANNOTATION_BYTES
        )
        validation.canonical_annotation(exact)

        over = annotation_of_exact_size(validation.MAX_ANNOTATION_BYTES + 1)
        with self.assertRaises(validation.PublicationValidationError):
            validation.canonical_annotation(over)

    def test_json_depth_is_bounded_at_eight_containers_and_keys_are_strings(self):
        def nested(depth):
            value = {}
            for _ in range(depth - 1):
                value = {"a": value}
            return value

        self.assertEqual(validation.json_depth(nested(8)), 8)
        self.assertEqual(validation.json_depth(nested(9)), 9)
        self.assertEqual(validation.json_depth(nested(500)), validation.MAX_JSON_CONTAINERS + 1)

        cycle = ann()
        cycle["shapes"] = [cycle]
        self.assertEqual(
            validation.json_depth(cycle), validation.MAX_JSON_CONTAINERS + 1
        )

        for value in ({1: "not-a-string-key"}, {"a": {2: "nested"}}, {None: 1}):
            with self.subTest(value=value):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.canonical_annotation(value)


AUTHOR = "clinician@example.test"
SITE = "clinic-photo-accept.local"
REQUEST_KEY = "request-key-0001"
SYNTHETIC_PATIENT = "SYNTHETIC-PATIENT-1"


def annotation_payload():
    return {
        "kind": "annotation_append",
        "request_key": REQUEST_KEY,
        "draft": "DRAFT-1",
        "expected_version": 0,
        "original": "PHOTO-1",
        "annotation": ann(rectangle()),
    }


def synthetic_payload():
    return {
        "kind": "synthetic_original_create",
        "request_key": "request-key-0002",
        "synthetic_patient": SYNTHETIC_PATIENT,
        "image_base64": PNG_1X1_BASE64,
    }


class WireRequestTests(unittest.TestCase):
    def test_annotation_wire_value_equals_direct_construction(self):
        payload = annotation_payload()
        request = contracts.validate_request(payload)
        canonical = validation.canonical_annotation(payload["annotation"])

        self.assertEqual(request.annotation, canonical)
        self.assertIsNone(request.image_bytes)
        self.assertIsNone(request.synthetic_patient)
        self.assertEqual(
            request,
            contracts.SaveRequest(
                kind="annotation_append",
                request_key=REQUEST_KEY,
                draft="DRAFT-1",
                expected_version=0,
                original="PHOTO-1",
                annotation=canonical,
            ),
        )
        self.assertEqual(
            contracts.validate_request_document(json.dumps(payload)), request
        )

    def test_synthetic_transport_decodes_once_and_equals_direct_construction(self):
        payload = synthetic_payload()
        request = contracts.validate_request(payload)

        self.assertEqual(request.image_bytes, PNG_1X1_BYTES)
        self.assertIsNone(request.draft)
        self.assertIsNone(request.expected_version)
        self.assertIsNone(request.original)
        self.assertIsNone(request.annotation)
        self.assertEqual(
            request,
            contracts.SaveRequest(
                kind="synthetic_original_create",
                request_key="request-key-0002",
                synthetic_patient=SYNTHETIC_PATIENT,
                image_bytes=PNG_1X1_BYTES,
            ),
        )
        self.assertEqual(
            contracts.validate_request_document(json.dumps(payload).encode("utf-8")),
            request,
        )

    def test_unknown_and_client_authority_wire_fields_are_rejected(self):
        for fieldname in (
            "owner",
            "site",
            "actor",
            "user",
            "generation",
            "path",
            "key",
            "storage_key",
            "permissions",
            "permission_flags",
            "ignore_permissions",
            "synthetic",
            "synthetic_mode",
            "image_bytes",
            "file_name",
            "is_private",
        ):
            with self.subTest(fieldname=fieldname):
                payload = annotation_payload()
                payload[fieldname] = "client-supplied"
                with self.assertRaises(contracts.PublicationValidationError) as caught:
                    contracts.validate_request(payload)
                self.assertEqual(str(caught.exception), contracts.SAFE_VALIDATION)

        raw = synthetic_payload()
        raw["image_bytes"] = PNG_1X1_BYTES
        with self.assertRaises(contracts.PublicationValidationError):
            contracts.validate_request(raw)

    def test_wire_and_direct_construction_share_the_same_bounds(self):
        direct = dict(
            kind="annotation_append",
            request_key=REQUEST_KEY,
            draft="DRAFT-1",
            expected_version=0,
            original="PHOTO-1",
            annotation=validation.canonical_annotation(ann(rectangle())),
        )

        wire_cases = (
            {**annotation_payload(), "request_key": "short"},
            {**annotation_payload(), "draft": "D" * 141},
            {**annotation_payload(), "annotation": ann(shape("freehand"))},
            {**annotation_payload(), "annotation": '{"shapes": [], "version": 1}'},
            {**annotation_payload(), "expected_version": True},
            {**synthetic_payload(), "image_base64": "data:image/png;base64," + PNG_1X1_BASE64},
            {**synthetic_payload(), "synthetic_patient": " " + SYNTHETIC_PATIENT},
        )
        for payload in wire_cases:
            with self.subTest(payload=payload):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

        direct_cases = (
            {**direct, "request_key": "short"},
            {**direct, "original": "P" * 141},
            {**direct, "annotation": ann(rectangle())},
            {**direct, "annotation": '{"shapes": [], "version": 1}'},
            {**direct, "expected_version": True},
            {**direct, "annotation": direct["annotation"] + " "},
        )
        for values in direct_cases:
            with self.subTest(values=values):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.SaveRequest(**values)

        for image in (b"not an image", bytearray(PNG_1X1_BYTES), PNG_1X1_BYTES[:20]):
            with self.subTest(image=image):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.SaveRequest(
                        kind="synthetic_original_create",
                        request_key="request-key-0002",
                        synthetic_patient=SYNTHETIC_PATIENT,
                        image_bytes=image,
                    )

    def test_request_document_is_bounded_before_parsing(self):
        with self.assertRaises(contracts.PublicationValidationError):
            contracts.validate_request_document("x" * (validation.MAX_REQUEST_BYTES + 1))
        for document in (b"\xff\xfe", b'{"kind": ', "", "[]", None):
            with self.subTest(document=document):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request_document(document)


class RecursionSafetyTests(unittest.TestCase):
    """Deep and cyclic input must deny with the fixed message, never overflow."""

    @staticmethod
    def deep_annotation(depth):
        value = []
        for _ in range(depth):
            value = [value]
        annotation = ann()
        annotation["unbounded"] = value
        return annotation

    def test_over_deep_annotation_denies_at_every_public_seam(self):
        payload = {**annotation_payload(), "annotation": self.deep_annotation(500)}
        text = json.dumps(payload)
        self.assertLess(len(text), validation.MAX_REQUEST_BYTES)

        for call in (
            lambda: validation.canonical_annotation(payload["annotation"]),
            lambda: validation.validate_request(payload),
            lambda: contracts.validate_request(payload),
            lambda: contracts.validate_request_document(text),
            lambda: contracts.validate_request_document(text.encode("utf-8")),
            lambda: contracts.SaveRequest(
                **{
                    key: value
                    for key, value in payload.items()
                    if key != "annotation"
                },
                annotation=json.dumps(payload["annotation"]),
            ),
        ):
            with self.subTest(call=call):
                with self.assertRaises(contracts.PublicationValidationError) as caught:
                    call()
                self.assertEqual(str(caught.exception), contracts.SAFE_VALIDATION)

    def test_cyclic_annotation_denies_instead_of_overflowing(self):
        cycle = ann()
        cycle["shapes"] = [cycle]

        self.assertGreater(
            validation.json_depth(cycle), validation.MAX_JSON_CONTAINERS
        )
        for call in (
            lambda: validation.canonical_annotation(cycle),
            lambda: validation.validate_request(
                {**annotation_payload(), "annotation": cycle}
            ),
            lambda: contracts.validate_request(
                {**annotation_payload(), "annotation": cycle}
            ),
        ):
            with self.subTest(call=call):
                with self.assertRaises(contracts.PublicationValidationError) as caught:
                    call()
                self.assertEqual(str(caught.exception), contracts.SAFE_VALIDATION)


class SharedSeamTests(unittest.TestCase):
    def test_validation_validate_request_accepts_dict_text_and_bytes(self):
        payload = annotation_payload()
        expected = contracts.validate_request(payload)
        document = json.dumps(payload)

        self.assertEqual(validation.validate_request(payload), expected)
        self.assertEqual(validation.validate_request(document), expected)
        self.assertEqual(validation.validate_request(document.encode("utf-8")), expected)

        synthetic = synthetic_payload()
        synthetic_expected = contracts.validate_request(synthetic)
        self.assertEqual(validation.validate_request(json.dumps(synthetic)), synthetic_expected)

    def test_shared_seam_applies_the_request_cap_before_parsing(self):
        cap = validation.MAX_REQUEST_BYTES
        self.assertEqual(validation.check_request_size("x" * cap), "x" * cap)

        for document in (
            "x" * (cap + 1),
            b"x" * (cap + 1),
            "é" * (cap // 2 + 1),
            b"\xff" * (cap + 1),
        ):
            with self.subTest(length=len(document)):
                with self.assertRaises(contracts.PublicationValidationError) as caught:
                    validation.validate_request(document)
                self.assertEqual(str(caught.exception), contracts.SAFE_VALIDATION)


class KindFieldPresenceTests(unittest.TestCase):
    def test_kind_inapplicable_wire_fields_must_be_absent_not_null(self):
        for fieldname in ("synthetic_patient", "image_base64"):
            with self.subTest(fieldname=fieldname):
                payload = annotation_payload()
                payload[fieldname] = None
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

        for fieldname in ("draft", "expected_version", "original", "annotation"):
            with self.subTest(fieldname=fieldname):
                payload = synthetic_payload()
                payload[fieldname] = None
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)


class SubmissionRecordTests(unittest.TestCase):
    @staticmethod
    def submitted(**values):
        request = contracts.validate_request(annotation_payload())
        base = dict(
            id="0123456789abcdef0123456789abcdef",
            site="clinic-photo-accept.local",
            actor=AUTHOR,
            kind=request.kind,
            request_key=request.request_key,
            payload_digest="a" * 64,
            request=request,
        )
        base.update(values)
        return contracts.SubmittedOperation(**base)

    def test_submitted_operation_binds_site_and_the_typed_request(self):
        operation = self.submitted()

        self.assertEqual(operation.state, "SUBMITTED")
        self.assertEqual(operation.site, "clinic-photo-accept.local")
        self.assertEqual(operation.request_key, REQUEST_KEY)
        self.assertIsInstance(operation.request, contracts.SaveRequest)
        self.assertEqual(operation.kind, operation.request.kind)
        self.assertEqual(operation.request_key, operation.request.request_key)
        self.assertIn("SUBMITTED", contracts.DURABLE_STATES)

    def test_submitted_operation_rejects_inconsistent_or_unbound_values(self):
        for values in (
            {"id": "A" * 32},
            {"id": "a" * 31},
            {"id": "g" * 32},
            {"site": ""},
            {"site": " padded "},
            {"actor": ""},
            {"kind": "synthetic_original_create"},
            {"request_key": "request-key-0009"},
            {"payload_digest": "A" * 64},
            {"payload_digest": "a" * 63},
            {"request": {"kind": "annotation_append"}},
            {"request": None},
            {"state": "PREPARED"},
            {"state": "submitted"},
        ):
            with self.subTest(values=values):
                with self.assertRaises(contracts.PublicationValidationError):
                    self.submitted(**values)

    def test_rejected_outcome_and_prepared_state_pairing(self):
        self.assertEqual(
            contracts.SAVE_STATUSES,
            {"pending", "saved", "conflict", "rejected", "unknown"},
        )
        self.assertEqual(contracts.SaveOutcome("rejected", "OP-1").status, "rejected")
        with self.assertRaises(contracts.PublicationValidationError):
            contracts.SaveOutcome("rejected", "")

        prepared = contracts.PreparedOperation(
            id="OP-1",
            site=SITE,
            actor=AUTHOR,
            kind="synthetic_original_create",
            payload_digest="a" * 64,
            key="/private/files/clinic-photo-1.png",
            generation=1,
            draft=None,
            expected_version=None,
            synthetic_patient="SYNTHETIC-PATIENT-1",
            target_identity="patient-creation:SYNTHETIC-PATIENT-1",
            expected_sha256="b" * 64,
            expected_size=1,
            state="PREPARED",
        )
        self.assertIsNone(prepared.draft)

        for state in ("PREPARED", "PUBLISHED", "ABORTED", "QUARANTINED"):
            with self.subTest(state=state):
                contracts.PreparedOperation(
                    id="OP-1",
                    site=SITE,
                    actor=AUTHOR,
                    kind="annotation_append",
                    payload_digest="a" * 64,
                    key="/private/files/clinic-annotation-1.json",
                    generation=1,
                    draft="DRAFT-1",
                    expected_version=0,
                    synthetic_patient=None,
                    target_identity="draft:DRAFT-1",
                    expected_sha256="b" * 64,
                    expected_size=1,
                    state=state,
                )

        for values in ({"state": "SUBMITTED"}, {"state": "PUBLISHED", "draft": ""}):
            with self.subTest(values=values):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.PreparedOperation(
                        **{
                            "id": "OP-1",
                            "site": SITE,
                            "actor": AUTHOR,
                            "kind": "annotation_append",
                            "payload_digest": "a" * 64,
                            "key": "/private/files/clinic-annotation-1.json",
                            "generation": 1,
                            "draft": "DRAFT-1",
                            "expected_version": 0,
                            "synthetic_patient": None,
                            "target_identity": "draft:DRAFT-1",
                            "expected_sha256": "b" * 64,
                            "expected_size": 1,
                            **values,
                        }
                    )
