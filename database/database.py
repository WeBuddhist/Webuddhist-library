from logging import getLogger
from typing import TYPE_CHECKING, Self

from neo4j import AsyncGraphDatabase, AsyncSession

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

        get_session = self.get_session
        self.transactions = SessionRepository(get_session)
        self.api_key = ApiKeyDatabase(db=self)
        self.application = ApplicationDatabase(get_session)
        self.text = TextDatabase(get_session)
        self.edition = EditionDatabase(get_session)
        self.annotation = AnnotationDatabase(get_session)
        self.alignment = AlignmentDatabase(get_session)
        self.segment = SegmentDatabase(get_session)
        self.audio = AudioDatabase(get_session)
        self.recording = RecordingDatabase(get_session)
        self.person = PersonDatabase(get_session)
        self.language = LanguageDatabase(get_session)
        self.category = CategoryDatabase(get_session)
        self.tag = TagDatabase(get_session)
        self.span = SpanDatabase()

    @classmethod
    def from_settings(cls, config: Settings) -> Self:
        return cls(config.neo4j_uri, (config.neo4j_username, config.neo4j_password), config.neo4j_database)

    async def verify_connectivity(self) -> None:
        await self._driver.verify_connectivity()
        logger.info("Async connection to neo4j established.")

    def get_session(self) -> AsyncSession:
        return self._driver.session(database=self._database)

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
