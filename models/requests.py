from typing import Self

from pydantic import Field, field_validator, model_validator

from .annotation import (
    PaginationInput,
    SegmentationInput,
)
from .base import LanguageFilter, NonEmptyStr, OpenPechaModel
from .edition import EditionInput
from .enums import EditionType


class PaginationParams(OpenPechaModel):
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of items to return")
    offset: int = Field(default=0, ge=0, description="Number of items to skip")


class AnnotationSegmentsPaginationParams(PaginationParams):
    limit: int = Field(default=500, ge=1, le=500, description="Maximum number of segments to return")


class AlignmentPaginationParams(AnnotationSegmentsPaginationParams):
    pass


class TextFacetFilter(OpenPechaModel):
    category_id: str | None = None
    tag_id: list[str] = Field(
        default=[],
        description="Application tag IDs. Repeat the parameter for groups that must all match; "
        "within a group, comma-separated IDs are alternatives. `tag_id=A,B&tag_id=C` means (A or B) and C.",
    )
    author_id: str | None = None
    bdrc: str | None = None
    wiki: str | None = None

    @field_validator("tag_id")
    @classmethod
    def validate_tag_id(cls, value: list[str]) -> list[str]:
        if any(not tag_id.strip() for group in value for tag_id in group.split(",")):
            raise ValueError("each tag_id must be a comma-separated list of non-empty tag IDs")
        return value

    @property
    def tag_groups(self) -> list[list[str]]:
        return [[tag_id.strip() for tag_id in group.split(",")] for group in self.tag_id]


class TextFilter(TextFacetFilter):
    language: LanguageFilter | None = None
    title: NonEmptyStr | None = Field(default=None, min_length=2, description="Filter by title, minimum 2 characters")


class TextsQueryParams(PaginationParams, TextFilter):
    pass


class PersonFilter(OpenPechaModel):
    name: NonEmptyStr | None = Field(
        default=None, min_length=2, description="Filter by person name, minimum 2 characters"
    )
    bdrc: str | None = Field(None, description="Filter by BDRC ID")
    wiki: str | None = Field(None, description="Filter by Wiki ID")


class PersonsQueryParams(PaginationParams, PersonFilter):
    pass


class EditionsQueryParams(OpenPechaModel):
    edition_type: EditionType | None = Field(None, description="Filter by edition type")


class RelatedSegmentsFilter(OpenPechaModel):
    text_id: str | None = Field(default=None, description="Filter related segments by text ID")
    edition_id: str | None = Field(default=None, description="Filter related segments by edition ID")
    language: LanguageFilter | None = Field(default=None, description="Filter related segments by text language")


class DirectRelatedSegmentsQueryParams(PaginationParams, RelatedSegmentsFilter):
    pass


class EditionRequestModel(OpenPechaModel):
    metadata: EditionInput
    pagination: PaginationInput | None = None
    segmentation: SegmentationInput | None = None
    content: NonEmptyStr

    @model_validator(mode="after")
    def validate_spans_within_content(self) -> Self:
        for annotation in (self.segmentation, self.pagination):
            if annotation is not None and annotation.max_end > len(self.content):
                raise ValueError(
                    f"annotation spans extend to {annotation.max_end} but content is {len(self.content)} characters; "
                    "spans must be Unicode code point offsets into the submitted content"
                )
        return self

    @model_validator(mode="after")
    def validate_annotation(self) -> Self:
        if self.metadata.type is EditionType.CRITICAL:
            if not self.segmentation:
                raise ValueError("Critical editions must have segmentation_annotation")
            if self.pagination:
                raise ValueError("Critical editions must not have pagination_annotation")
        elif self.metadata.type is EditionType.DIPLOMATIC and not self.pagination:
            raise ValueError("Diplomatic editions must have pagination_annotation")

        return self


class LanguageCreateRequest(OpenPechaModel):
    code: NonEmptyStr
    name: NonEmptyStr


class ApplicationCreateRequest(OpenPechaModel):
    name: NonEmptyStr


class SegmentsQueryParams(OpenPechaModel):
    search_type: str = Field(default="semantic", description="Type of segment search")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum number of results")
    return_text: bool = Field(default=True, description="Include text content")
    title: str | None = Field(None, description="Filter by title")
