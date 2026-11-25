from .base import LocalizedString, NonEmptyStr, OpenPechaModel


class TagInput(OpenPechaModel):
    title: LocalizedString
    description: LocalizedString | None = None


class TagOutput(TagInput):
    id: NonEmptyStr
