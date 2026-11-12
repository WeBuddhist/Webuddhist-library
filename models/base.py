from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictStr,
    StringConstraints,
    model_validator,
)

type LanguageFilter = Annotated[str, StringConstraints(to_lower=True, pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")]

type NonEmptyStr = Annotated[StrictStr, StringConstraints(min_length=1, pattern=r"\S")]


def normalize_language_tag(tag: str) -> str:
    """Return a language tag in standard BCP 47 casing: ``EN`` -> ``en``, ``en-us`` -> ``en-US``,
    ``zh-hans`` -> ``zh-Hans``. Subtags after a singleton (``x-``, ``u-``...) stay lowercase."""
    language, *subtags = tag.split("-")
    normalized = [language.lower()]
    after_singleton = False
    for subtag in subtags:
        after_singleton = after_singleton or len(subtag) == 1
        if not after_singleton and len(subtag) == 4 and subtag.isalpha():
            normalized.append(subtag.title())  # script
        elif not after_singleton and (
            (len(subtag) == 2 and subtag.isalpha()) or (len(subtag) == 3 and subtag.isdigit())
        ):
            normalized.append(subtag.upper())  # region
        else:
            normalized.append(subtag.lower())
    return "-".join(normalized)


def normalize_localization_keys(value: object, field: str) -> object:
    """Normalize the language keys of a raw localized-string dict, rejecting two keys for one language."""
    if not isinstance(value, dict):
        return value
    normalized: dict[object, object] = {}
    for language, text in value.items():
        key = normalize_language_tag(language) if isinstance(language, str) else language
        if key in normalized:
            raise ValueError(f"{field} has more than one localization for language '{key}'")
        normalized[key] = text
    return normalized


def _dedupe[T](items: list[T], exclude: T) -> list[T]:
    """Remove duplicates and excluded item from list."""
    seen: list[T] = []
    for item in items:
        if item != exclude and item not in seen:
            seen.append(item)
    return seen


class OpenPechaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchModel(OpenPechaModel):
    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, data: object) -> object:
        if isinstance(data, dict):
            null_fields = [str(field) for field, value in data.items() if value is None]
            if null_fields:
                raise ValueError(f"Null values are not supported in PATCH: {', '.join(null_fields)}")
        return data

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided for update")
        return self


class LocalizedString(RootModel[dict[str, NonEmptyStr]]):
    root: dict[str, NonEmptyStr] = Field(min_length=1)

    def __getitem__(self, item: str) -> str:
        return self.root[item]
