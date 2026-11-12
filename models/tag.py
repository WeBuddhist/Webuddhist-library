from pydantic import Field

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel


class TagBase(OpenPechaModel):
    title: LocalizedString
    description: LocalizedString | None = None


class TagInput(TagBase):
    pass


class TagOutput(TagBase):
    id: NonEmptyStr
    group_id: str | None = None


class TagGroupInput(TagBase):
    pass


class TagGroupPatch(PatchModel):
    """A sent `title` or `description` replaces that field's localizations as a whole."""

    title: LocalizedString | None = None
    description: LocalizedString | None = None


class TagGroupOutput(TagBase):
    id: NonEmptyStr
    tag_ids: list[str] = Field(default_factory=list)
