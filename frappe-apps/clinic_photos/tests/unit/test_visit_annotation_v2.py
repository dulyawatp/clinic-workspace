import json

import pytest

from clinic_photos.publication import validation
from clinic_photos.publication.validation import (
    PublicationValidationError,
    canonical_annotation,
)


def document(*shapes, version=2):
    return {"schema_version": version, "coordinate_space": "normalized", "shapes": list(shapes)}


def stroke(points=None, **changes):
    value = {
        "id": "stroke",
        "type": "freehand",
        "color": "#123456",
        "stroke_width": 0.01,
        "points": points if points is not None else [{"x": 0, "y": 1}, {"x": 1, "y": 0}],
    }
    value.update(changes)
    return value


def test_annotation_v2_freehand_round_trip_and_v1_rejects_it():
    rectangle = {"id": "rect", "type": "rectangle", "color": "#abcdef",
                 "x": 0, "y": 0, "width": 1, "height": 1, "stroke_width": 0.1}
    value = document(stroke(), rectangle)
    assert json.loads(validation.canonical_visit_annotation(value)) == value
    with pytest.raises(PublicationValidationError):
        canonical_annotation(document(stroke(), version=1))
    with pytest.raises(PublicationValidationError):
        canonical_annotation(value)
    with pytest.raises(PublicationValidationError):
        validation.canonical_visit_annotation(document(version=1))
    assert json.loads(canonical_annotation(document(rectangle, version=1))) == document(rectangle, version=1)


@pytest.mark.parametrize("shape", [
    stroke(extra=1),
    stroke(points=[]),
    stroke(points=[{"x": 0, "y": 0}]),
    stroke(points=[{"x": float("nan"), "y": 0}, {"x": 0, "y": 0}]),
    stroke(points=[{"x": 1.01, "y": 0}, {"x": 0, "y": 0}]),
    stroke(points=[{"x": 0, "y": 0, "z": 1}, {"x": 0, "y": 0}]),
    stroke(points=[{"x": 10**400, "y": 0}, {"x": 0, "y": 0}]),
    stroke(points=[{"x": False, "y": 0}, {"x": 0, "y": 0}]),
    stroke(stroke_width=0),
    stroke(points=[{"x": 0, "y": 0}] * 1025),
])
def test_annotation_v2_rejects_invalid_freehand(shape):
    with pytest.raises(PublicationValidationError):
        validation.canonical_visit_annotation(document(shape))


def test_annotation_v2_point_budget_and_unique_ids():
    points = [{"x": 0, "y": 0}] * 1024
    validation.canonical_visit_annotation(document(*(stroke(points, id=str(i)) for i in range(8))))
    with pytest.raises(PublicationValidationError):
        validation.canonical_visit_annotation(document(*(stroke(points, id=str(i)) for i in range(9))))
    with pytest.raises(PublicationValidationError):
        validation.canonical_visit_annotation(document(stroke(), stroke()))


def test_annotation_v2_rejects_overflow_in_non_freehand_shape():
    rectangle = {"id": "rect", "type": "rectangle", "color": "#abcdef",
                 "x": 10**400, "y": 0, "width": 1, "height": 1, "stroke_width": 0.1}
    with pytest.raises(PublicationValidationError):
        validation.canonical_visit_annotation(document(rectangle))
    with pytest.raises(OverflowError):
        canonical_annotation(document(rectangle, version=1))


def test_annotation_v2_retains_document_bounds():
    shapes = [stroke(id=str(i)) for i in range(512)]
    validation.canonical_visit_annotation(document(*shapes))
    with pytest.raises(PublicationValidationError):
        validation.canonical_visit_annotation(document(*shapes, stroke(id="extra")))
    with pytest.raises(PublicationValidationError):
        validation.canonical_visit_annotation(document(stroke(points=[{"x": [[[[[0]]]]], "y": 0}] * 2)))
    with pytest.raises(PublicationValidationError):
        validation.canonical_visit_annotation(document(*(
            stroke([{"x": 0.1234567890123456, "y": 0.1234567890123456}] * 1024, id=str(i))
            for i in range(8)
        )))
