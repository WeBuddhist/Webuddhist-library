from pydantic import MISSING, Field

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel


class CategoryInput(OpenPechaModel):
    title: LocalizedString
    description: LocalizedString | None = None
    parent_id: NonEmptyStr | None = None


class CategoryPatch(PatchModel):
    title: LocalizedString | MISSING = MISSING
    description: LocalizedString | MISSING = MISSING


class CategoryOutput(CategoryInput):
    id: NonEmptyStr
    children: list[str] = Field(default_factory=list)
