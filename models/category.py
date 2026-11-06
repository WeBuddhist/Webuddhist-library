from pydantic import Field

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel


class CategoryBase(OpenPechaModel):
    title: LocalizedString
    description: LocalizedString | None = None
    parent_id: NonEmptyStr | None = None


class CategoryInput(CategoryBase):
    pass


class CategoryPatch(PatchModel):
    title: LocalizedString | None = None
    description: LocalizedString | None = None


class CategoryOutput(CategoryBase):
    id: NonEmptyStr
    children: list[str] = Field(default_factory=list)
