"""Optional updates after an HTTP mutation. Failures are logged; manual reindexing repairs misses."""

import logging
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, Request

from database import text

logger = logging.getLogger(__name__)
type SearchKind = Literal["person", "text", "work", "edition"]


class SearchUpdates:
    def __init__(self, request: Request, background_tasks: BackgroundTasks) -> None:
        self._request = request
        self._background_tasks = background_tasks

    def schedule(self, kind: SearchKind, entity_id: str) -> None:
        self._background_tasks.add_task(update_search, self._request, kind, entity_id)


SearchUpdatesDep = Annotated[SearchUpdates, Depends()]


async def update_search(request: Request, kind: SearchKind, entity_id: str) -> None:
    state = request.app.state
    try:
        if kind == "edition":
            service = getattr(state, "content_search", None)
            if service is not None:
                await service.index_edition(entity_id, state.db, state.storage)
            return
        service = getattr(state, "catalog_search", None)
        if service is None:
            return
        ids = [entity_id]
        if kind in ("text", "work"):
            ids = await text.get_work_text_ids(state.db, kind, entity_id)
            if kind == "text" and entity_id not in ids:
                ids.append(entity_id)
            kind = "text"
        for document_id in ids:
            await service.index_entity(kind, document_id, state.db)
    except Exception:
        logger.exception("Search update failed for %s %s; reindex to repair", kind, entity_id)
