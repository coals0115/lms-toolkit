"""숭실사이버대 LMS Provider (stub — Phase 3에서 구현)"""

import logging

from playwright.async_api import Page

from ..config import SchoolConfig
from ..types import Course, Lecture, ProcessResult

logger = logging.getLogger(__name__)


class KCUProvider:
    def __init__(self, config: SchoolConfig):
        self._config = config
        self._base_url = config.base_url

    @property
    def name(self) -> str:
        return "kcu"

    @property
    def display_name(self) -> str:
        return "숭실사이버대"

    def get_credentials(self) -> tuple[str, str]:
        return (self._config.userid or "", self._config.password or "")

    async def login(self, page: Page) -> None:
        raise NotImplementedError("KCU Provider는 아직 구현되지 않았습니다")

    async def get_courses(self, page: Page) -> list[Course]:
        raise NotImplementedError("KCU Provider는 아직 구현되지 않았습니다")

    async def get_lectures(
        self, page: Page, course_id: str, course_name: str = ""
    ) -> list[Lecture]:
        raise NotImplementedError("KCU Provider는 아직 구현되지 않았습니다")

    async def process_lecture(self, page: Page, lecture: Lecture) -> ProcessResult:
        raise NotImplementedError("KCU Provider는 아직 구현되지 않았습니다")
