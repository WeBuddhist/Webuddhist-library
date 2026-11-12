from pydantic import Field, ValidationInfo, field_validator

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel, normalize_localization_keys


class TagBase(OpenPechaModel):
    title: LocalizedString
    description: LocalizedString | None = None


class TagInput(TagBase):
    pass


class TagOutput(TagBase):
    id: NonEmptyStr
    group_id: str | None = None


class TagGroupInput(TagBase):
    """Language codes are stored in standard BCP 47 casing, one localization per language."""

    @field_validator("title", "description", mode="before")
    @classmethod
    def normalize_languages(cls, value: object, info: ValidationInfo) -> object:
        return normalize_localization_keys(value, str(info.field_name))


class TagGroupPatch(PatchModel):
    """Localizations are merged per language: sent languages are added or replaced, the rest are kept."""

    title: LocalizedString | None = None
    description: LocalizedString | None = None

    @field_validator("title", "description", mode="before")
    @classmethod
    def normalize_languages(cls, value: object, info: ValidationInfo) -> object:
        return normalize_localization_keys(value, str(info.field_name))


class TagGroupOutput(TagBase):
    id: NonEmptyStr
    tag_ids: list[str] = Field(default_factory=list)
