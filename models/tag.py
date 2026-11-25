from .base import LocalizedString, NonEmptyStr, OpenPechaModel


class TagBase(OpenPechaModel):
    title: LocalizedString
    description: LocalizedString | None = None


class TagInput(TagBase):
    pass


class TagOutput(TagBase):
    id: NonEmptyStr
