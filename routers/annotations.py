from typing import Annotated

from fastapi import APIRouter, Path, status

from dependencies import DatabaseDep
from models.annotation import (
    BibliographicMetadataOutput,
    FootnoteOutput,
    MarkOutput,
    NoteOutput,
    PaginationOutput,
    TableOfContentsOutput,
)
from models.enums import NoteType

router = APIRouter(prefix="/v2", tags=["Annotations"])

PaginationId = Annotated[str, Path(description="The ID of the pagination")]
TableOfContentsId = Annotated[str, Path(description="The ID of the table of contents")]
BibliographicId = Annotated[str, Path(description="The ID of the bibliographic annotation")]
DurchenId = Annotated[str, Path(description="The ID of the durchen annotation")]
FootnoteId = Annotated[str, Path(description="The ID of the footnote annotation")]
YigchungId = Annotated[str, Path(description="The ID of the yigchung annotation")]


@router.get(
    "/paginations/{pagination_id}", summary="Get pagination", description="Retrieve a pagination annotation by ID."
)
async def get_pagination(pagination_id: PaginationId, db: DatabaseDep) -> PaginationOutput:
    return await db.annotation.pagination.get(pagination_id)


@router.delete(
    "/paginations/{pagination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete pagination",
    description="Delete a pagination annotation.",
)
async def delete_pagination(pagination_id: PaginationId, db: DatabaseDep) -> None:
    await db.annotation.pagination.delete(pagination_id)


@router.get(
    "/table-of-contents/{toc_id}",
    summary="Get table of contents",
    description="Retrieve a table of contents annotation by ID.",
    response_model_exclude_none=True,
)
async def get_table_of_contents(toc_id: TableOfContentsId, db: DatabaseDep) -> TableOfContentsOutput:
    return await db.annotation.table_of_contents.get(toc_id)


@router.delete(
    "/table-of-contents/{toc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete table of contents",
    description="Delete a table of contents annotation.",
)
async def delete_table_of_contents(toc_id: TableOfContentsId, db: DatabaseDep) -> None:
    await db.annotation.table_of_contents.delete(toc_id)


@router.get(
    "/bibliographic/{bibliographic_id}",
    summary="Get bibliographic metadata annotation",
    description="Retrieve a bibliographic metadata annotation by ID.",
)
async def get_bibliographic(bibliographic_id: BibliographicId, db: DatabaseDep) -> BibliographicMetadataOutput:
    return await db.annotation.bibliographic.get(bibliographic_id)


@router.delete(
    "/bibliographic/{bibliographic_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete bibliographic metadata",
    description="Delete a bibliographic metadata annotation.",
)
async def delete_bibliographic(bibliographic_id: BibliographicId, db: DatabaseDep) -> None:
    await db.annotation.bibliographic.delete(bibliographic_id)


@router.get(
    "/durchens/{durchen_id}", summary="Get durchen annotation", description="Retrieve a durchen annotation by ID."
)
async def get_durchen(durchen_id: DurchenId, db: DatabaseDep) -> NoteOutput:
    return await db.annotation.note.get(durchen_id, NoteType.DURCHEN)


@router.delete(
    "/durchens/{durchen_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete durchen note",
    description="Delete a durchen note annotation.",
)
async def delete_durchen(durchen_id: DurchenId, db: DatabaseDep) -> None:
    await db.annotation.note.delete(durchen_id, NoteType.DURCHEN)


@router.get(
    "/footnotes/{footnote_id}", summary="Get footnote annotation", description="Retrieve a footnote annotation by ID."
)
async def get_footnote(footnote_id: FootnoteId, db: DatabaseDep) -> FootnoteOutput:
    return FootnoteOutput.from_note(await db.annotation.note.get(footnote_id, NoteType.FOOTNOTE))


@router.delete(
    "/footnotes/{footnote_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete footnote annotation",
    description="Delete a footnote annotation.",
)
async def delete_footnote(footnote_id: FootnoteId, db: DatabaseDep) -> None:
    await db.annotation.note.delete(footnote_id, NoteType.FOOTNOTE)


@router.get(
    "/yigchungs/{yigchung_id}",
    summary="Get yigchung annotation",
    description="Retrieve a yigchung mark annotation by ID.",
)
async def get_yigchung(yigchung_id: YigchungId, db: DatabaseDep) -> MarkOutput:
    return await db.annotation.mark.get(yigchung_id)


@router.delete(
    "/yigchungs/{yigchung_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete yigchung annotation",
    description="Delete a yigchung mark annotation.",
)
async def delete_yigchung(yigchung_id: YigchungId, db: DatabaseDep) -> None:
    await db.annotation.mark.delete(yigchung_id)
