from logging import getLogger
from typing import TYPE_CHECKING, Any, LiteralString, Self

from neo4j import AsyncGraphDatabase, AsyncSession, Record, RoutingControl

from .alignment_database import AlignmentDatabase
from .annotation.bibliographic_database import BibliographicDatabase
from .annotation.mark_database import MarkDatabase
from .annotation.note_database import NoteDatabase
from .annotation.pagination_database import PaginationDatabase
from .annotation.segmentation_database import SegmentationDatabase
from .annotation.table_of_contents_database import TableOfContentsDatabase
from .api_key_database import ApiKeyDatabase
from .application_database import ApplicationDatabase
from .audio_database import AudioDatabase
from .category_database import CategoryDatabase
from .edition_database import EditionDatabase
from .language_database import LanguageDatabase
from .person_database import PersonDatabase
from .recording_database import RecordingDatabase
from .repository import SessionRepository, SessionSource
from .segment_database import SegmentDatabase
from .span_database import SpanDatabase
from .tag_database import TagDatabase
from .text_database import TextDatabase

if TYPE_CHECKING:
    from config import Settings

logger = getLogger(__name__)


class AnnotationDatabase:
    def __init__(self, db: SessionSource) -> None:
        self.segmentation = SegmentationDatabase(db)
        self.pagination = PaginationDatabase(db)
        self.table_of_contents = TableOfContentsDatabase(db)
        self.note = NoteDatabase(db)
        self.bibliographic = BibliographicDatabase(db)
        self.mark = MarkDatabase(db)


class Database:
    """Async database class for Neo4j operations."""

    def __init__(self, neo4j_uri: str, neo4j_auth: tuple[str, str], neo4j_database: str = "neo4j") -> None:
        self._driver = AsyncGraphDatabase.driver(neo4j_uri, auth=neo4j_auth)
        self._database = neo4j_database

        self.transactions = SessionRepository(self)
        self.api_key = ApiKeyDatabase(self)
        self.application = ApplicationDatabase(self)
        self.text = TextDatabase(self)
        self.edition = EditionDatabase(self)
        self.annotation = AnnotationDatabase(self)
        self.alignment = AlignmentDatabase(self)
        self.segment = SegmentDatabase(self)
        self.audio = AudioDatabase(self)
        self.recording = RecordingDatabase(self)
        self.person = PersonDatabase(self)
        self.language = LanguageDatabase(self)
        self.category = CategoryDatabase(self)
        self.tag = TagDatabase(self)
        self.span = SpanDatabase()

    @classmethod
    def from_settings(cls, config: Settings) -> Self:
        return cls(config.neo4j_uri, (config.neo4j_username, config.neo4j_password), config.neo4j_database)

    async def verify_connectivity(self) -> None:
        await self._driver.verify_connectivity()
        logger.info("Async connection to neo4j established.")

    def get_session(self) -> AsyncSession:
        return self._driver.session(database=self._database)

    async def execute_query(self, query: LiteralString, /, *, write: bool = False, **params: Any) -> list[Record]:  # noqa: ANN401
        routing = RoutingControl.WRITE if write else RoutingControl.READ
        result = await self._driver.execute_query(query, params, routing_=routing, database_=self._database)
        return result.records

    async def close(self) -> None:
        await self._driver.close()

    async def __aenter__(self) -> Self:
        try:
            await self.verify_connectivity()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, _exc_type: object, _exc_val: object, _exc_tb: object) -> None:
        await self.close()
