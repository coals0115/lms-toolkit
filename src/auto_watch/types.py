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


class Assignment(TypedDict):
    school: str
    course: str
    kind: str
    title: str
    start: str | None
    due: str | None
    submitted: bool | None
    missing: bool
    status: str
    url: str


class Notice(TypedDict):
    school: str
    course: str
    id: str
    title: str
    posted: str | None
    body: str
    url: str


class TranscriptResult(TypedDict):
    course: str
    title: str
    mp4: str | None
    txt: str | None
    skipped: bool
