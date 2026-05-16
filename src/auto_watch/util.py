"""범용 헬퍼"""


def format_duration(total_sec: int | float) -> str:
    """초를 H:MM:SS (1시간 이상) 또는 M:SS 형식으로 변환.

    float 입력(예: HTMLVideoElement.duration)을 허용하며 내부에서 int로 절삭.
    """
    total_sec = int(total_sec)
    total_m, s = divmod(total_sec, 60)
    h, m = divmod(total_m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
