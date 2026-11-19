import pytest
from pydantic import ValidationError

from models.requests import EditionRequestModel


@pytest.fixture
def diplomatic_request():
    content = "བོད་"
    return {
        "content": content,
        "metadata": {"type": "diplomatic", "bdrc": "W12345"},
        "pagination": {
            "volumes": [{"pages": [{"reference": "1a", "lines": [{"start": 0, "end": len(content)}]}]}]
        },
        "segmentation": {"segments": [{"lines": [{"start": 0, "end": len(content)}]}]},
    }


@pytest.mark.parametrize("annotation_kind", ["pagination", "segmentation"])
def test_creation_validates_both_annotation_bounds(diplomatic_request, annotation_kind):
    if annotation_kind == "pagination":
        lines = diplomatic_request["pagination"]["volumes"][0]["pages"][0]["lines"]
    else:
        lines = diplomatic_request["segmentation"]["segments"][0]["lines"]
    lines[0]["end"] = len(diplomatic_request["content"]) + 1

    with pytest.raises(ValidationError, match="annotation spans extend to 5 but content is 4 characters"):
        EditionRequestModel.model_validate(diplomatic_request)


@pytest.mark.parametrize("missing_field", ["pagination", "bdrc"])
def test_diplomatic_segmentation_does_not_replace_required_fields(diplomatic_request, missing_field):
    if missing_field == "pagination":
        diplomatic_request.pop("pagination")
        message = "Diplomatic editions must have pagination_annotation"
    else:
        diplomatic_request["metadata"].pop("bdrc")
        message = "When type is 'diplomatic', bdrc must be provided"

    with pytest.raises(ValidationError, match=message):
        EditionRequestModel.model_validate(diplomatic_request)
