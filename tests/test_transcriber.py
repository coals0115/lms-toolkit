"""전사본 문단 포맷 테스트"""

from src.audio_pipeline.transcriber import format_paragraphs


def test_groups_segments_into_paragraphs_with_start_time():
    segments = [(0.0, " 안녕하세요"), (10.0, " 오늘은"), (31.0, " 행렬을"), (45.0, " 봅니다")]
    assert format_paragraphs(segments) == "[0:00]\n안녕하세요 오늘은\n\n[0:31]\n행렬을 봅니다\n"


def test_hour_long_lecture_shows_hours():
    assert format_paragraphs([(3725.0, "끝")]) == "[1:02:05]\n끝\n"


def test_blank_segments_are_skipped():
    assert format_paragraphs([(0.0, "  "), (40.0, "내용")]) == "[0:40]\n내용\n"


def test_empty():
    assert format_paragraphs([]) == ""


def test_mlx_does_not_condition_on_previous_text(tmp_path, monkeypatch):
    import mlx_whisper

    from src.audio_pipeline.transcriber import WhisperTranscriber

    seen = {}

    def fake(audio, **kw):
        seen.update(kw)
        return {"segments": [{"start": 0.0, "text": "안녕"}]}

    monkeypatch.setattr(mlx_whisper, "transcribe", fake)
    t = WhisperTranscriber.__new__(WhisperTranscriber)
    t._use_mlx = True
    t.transcribe("a.mp4", str(tmp_path / "a.txt"))
    assert seen["condition_on_previous_text"] is False
