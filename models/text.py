from typing import Self

from pydantic import MISSING, Field, model_validator

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel, _dedupe, base_language_code
from .contribution import ContributionInputItem, ContributionOutputItem
from .enums import LicenseType


class TextBase(OpenPechaModel):
    bdrc: NonEmptyStr | None = None
    wiki: NonEmptyStr | None = None
    date: NonEmptyStr | None = None
    title: LocalizedString
    alt_titles: list[LocalizedString] | None = None
    language: NonEmptyStr
    commentary_of: NonEmptyStr | None = None
    translation_of: NonEmptyStr | None = None
    part_of: NonEmptyStr | None = Field(default=None, description="The larger text this text is a part of")
    license: LicenseType = LicenseType.PUBLIC_DOMAIN_MARK

    @model_validator(mode="after")
    def validate_text(self) -> Self:
        if self.commentary_of and self.translation_of:
            raise ValueError("Cannot be both a commentary and translation")
        if self.part_of and (self.commentary_of or self.translation_of):
            raise ValueError("A part of a text cannot also be a commentary or translation")

        if base_language_code(self.language) not in map(base_language_code, self.title.root):
            raise ValueError(
                f"Title must include an entry for the text's language '{self.language}'. "
                f"Available title languages: {list(self.title.root)}"
            )
        return self

    @model_validator(mode="after")
    def remove_duplicate_alt_titles(self) -> Self:
        if self.alt_titles is not None:
            self.alt_titles = _dedupe(list(self.alt_titles), self.title) or None
        return self


class TextInput(TextBase):
    category_id: NonEmptyStr | MISSING = MISSING
    contributions: list[ContributionInputItem] = Field(default_factory=list)
    tag_ids: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_category(self) -> Self:
        if self.translation_of:
            if self.category_id is not MISSING:
                raise ValueError("Translations inherit their category; category_id must not be supplied")
        elif self.category_id is MISSING:
            raise ValueError("category_id is required for original texts and commentaries")
        return self


class TextPatch(PatchModel):
    bdrc: NonEmptyStr | MISSING = MISSING
    wiki: NonEmptyStr | MISSING = MISSING
    date: NonEmptyStr | MISSING = MISSING
    title: LocalizedString | MISSING = MISSING
    alt_titles: list[LocalizedString] | MISSING = MISSING
    language: NonEmptyStr | MISSING = MISSING
    category_id: NonEmptyStr | MISSING = MISSING
    expected_category_id: NonEmptyStr | MISSING = MISSING
    license: LicenseType | MISSING = MISSING
    contributions: list[ContributionInputItem] | MISSING = MISSING
    tag_ids: list[NonEmptyStr] | MISSING = MISSING

    @model_validator(mode="after")
    def remove_duplicate_alt_titles(self) -> Self:
        if self.expected_category_id is not MISSING and self.category_id is MISSING:
            raise ValueError("expected_category_id requires category_id")
        if self.alt_titles is not MISSING and self.title is not MISSING:
            self.alt_titles = _dedupe(list(self.alt_titles), self.title)
        return self


class TextOutput(TextBase):
    id: NonEmptyStr
    category_id: NonEmptyStr
    contributions: list[ContributionOutputItem]
    commentaries: list[str] = Field(default_factory=list)
    translations: list[str] = Field(default_factory=list)
    parts: list[str] = Field(default_factory=list)
    editions: list[str] = Field(default_factory=list)
    audios: list[str] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
