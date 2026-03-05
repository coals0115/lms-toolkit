"""데이터 타입 정의"""

from typing import TypedDict


class Course(TypedDict):
    name: str
    courseId: str
    videoCount: int


class Lecture(TypedDict):
    title: str
    href: str
    isCompleted: bool
    durationSec: int
    itemType: str
    courseName: str
    startDate: str | None
    deadline: str | None


class ProcessResult(TypedDict):
    attended: bool
    download_only: bool
    mp4: str | None
    txt: str | None


class TranscriptResult(TypedDict):
    mp4: str | None
    txt: str | None
