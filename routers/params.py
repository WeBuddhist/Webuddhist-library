from typing import Annotated

from fastapi import Path

AudioId = Annotated[str, Path(description="The ID of the audio")]
CategoryId = Annotated[str, Path(description="The ID of the category")]
EditionId = Annotated[str, Path(description="The ID of the edition")]
PersonId = Annotated[str, Path(description="The ID of the person")]
RecordingId = Annotated[str, Path(description="The ID of the recording")]
SegmentId = Annotated[str, Path(description="The ID of the segment")]
TagId = Annotated[str, Path(description="The ID of the tag")]
TextId = Annotated[str, Path(description="The ID of the text")]
