from itertools import pairwise
from typing import Annotated, Self

from pydantic import MISSING, AfterValidator, Field, model_validator

from .annotation import Span
from .base import LocalizedString, NonEmptyStr, OpenPechaModel, PatchModel
from .enums import AudioFormat


class RecordingInput(OpenPechaModel):
    """One audio file of an Audio. `title` names the version, for example "With music"."""

    title: LocalizedString | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    edition_id: NonEmptyStr = Field(description="Edition of the recited text that this recording follows.")


class RecordingPatch(PatchModel):
    title: LocalizedString | MISSING = MISSING
    duration_ms: int | MISSING = Field(default=MISSING, ge=0)


class RecordingOutput(RecordingInput):
    id: NonEmptyStr
    audio_id: NonEmptyStr
    text_id: NonEmptyStr
    format: AudioFormat
    size_bytes: int


class RecordingAudioUrl(OpenPechaModel):
    """A short-lived link to a recording's audio file that any player can open without an API key."""

    url: NonEmptyStr = Field(description="Presigned storage URL; supports range requests and seeking")
    expires_in: int = Field(description="Seconds until the URL stops working", gt=0)


class RecordingCue(OpenPechaModel):
    """The stretch of audio during which the text in `span` is being recited."""

    span: Span
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_cue(self) -> Self:
        if self.start_ms >= self.end_ms:
            raise ValueError("'start_ms' must be less than 'end_ms'")
        if self.span.start == self.span.end:
            raise ValueError("a cue span must cover at least one character")
        return self


def _validate_cues_disjoint(cues: list[RecordingCue]) -> list[RecordingCue]:
    for previous, current in pairwise(cues):
        if current.start_ms < previous.end_ms:
            raise ValueError(
                f"cues must be sorted and non-overlapping in time: [{previous.start_ms},{previous.end_ms}) "
                f"is followed by [{current.start_ms},{current.end_ms})"
            )
    return cues


RecordingCues = Annotated[list[RecordingCue], AfterValidator(_validate_cues_disjoint)]
