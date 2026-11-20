from typing import Self

from pydantic import MISSING, model_validator

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel, _dedupe


class PersonBase(OpenPechaModel):
    bdrc: NonEmptyStr | None = None
    wiki: NonEmptyStr | None = None
    name: LocalizedString
    alt_names: list[LocalizedString] | None = None

    @model_validator(mode="after")
    def remove_duplicate_alt_names(self) -> Self:
        if self.alt_names is not None:
            self.alt_names = _dedupe(list(self.alt_names), self.name) or None
        return self


class PersonInput(PersonBase):
    pass


class PersonPatch(PatchModel):
    bdrc: NonEmptyStr | MISSING = MISSING
    wiki: NonEmptyStr | MISSING = MISSING
    name: LocalizedString | MISSING = MISSING
    alt_names: list[LocalizedString] | MISSING = MISSING

    @model_validator(mode="after")
    def remove_duplicate_alt_names(self) -> Self:
        if self.alt_names is not MISSING and self.name is not MISSING:
            self.alt_names = _dedupe(list(self.alt_names), self.name)
        return self


class PersonOutput(PersonBase):
    id: NonEmptyStr
