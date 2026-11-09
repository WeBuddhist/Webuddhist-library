from typing import Annotated

from pydantic import AfterValidator, Field

from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel
from .contribution import ContributionInputItem, ContributionOutputItem
from .enums import AUDIO_ROLES, LicenseType


def _validate_performer_roles(contributions: list[ContributionInputItem]) -> list[ContributionInputItem]:
    """An Audio credits the people or AI performing the text, so only performer roles are accepted."""
    other_roles = sorted({c.role for c in contributions if c.role not in AUDIO_ROLES})
    if other_roles:
        raise ValueError(f"Audio contributions must have role {', '.join(AUDIO_ROLES)}, got: {', '.join(other_roles)}")
    return contributions


PerformerContributions = Annotated[
    list[ContributionInputItem], Field(min_length=1), AfterValidator(_validate_performer_roles)
]


class AudioBase(OpenPechaModel):
    title: LocalizedString | None = None
    language: NonEmptyStr | None = None
    license: LicenseType = LicenseType.PUBLIC_DOMAIN_MARK
    date: NonEmptyStr | None = None


class AudioInput(AudioBase):
    """One performance of a text. `language` defaults to the language of the recited text."""

    contributions: PerformerContributions


class AudioPatch(PatchModel):
    title: LocalizedString | None = None
    language: NonEmptyStr | None = None
    license: LicenseType | None = None
    date: NonEmptyStr | None = None
    contributions: PerformerContributions | None = None


class AudioOutput(AudioBase):
    id: NonEmptyStr
    text_id: NonEmptyStr
    contributions: list[ContributionOutputItem]
    recordings: list[NonEmptyStr]
