"""
버튼 컴포넌트들
"""

from PySide6.QtWidgets import QPushButton

from src.gui.config.styles import StyleSheet
from src.gui.config.constants import EMOJI_START, EMOJI_PROCESSING


class ProcessingButton(QPushButton):
    """처리 작업용 버튼"""

    def __init__(self, text: str = f"{EMOJI_START} 요약 시작"):
        super().__init__(text)
        self.default_text = text
        self.processing_text = f"{EMOJI_PROCESSING} 처리 중..."
        self.setStyleSheet(StyleSheet.button())
        self.is_processing = False

    def start_processing(self):
        """처리 시작 상태로 변경"""
        self.is_processing = True
        self.setText(self.processing_text)
        self.setEnabled(False)

    def stop_processing(self):
        """처리 완료 상태로 변경"""
        self.is_processing = False
        self.setText(self.default_text)
        self.setEnabled(True)

    def set_enabled_with_text(self, enabled: bool, text: str = None):
        """활성화 상태와 텍스트 동시 설정"""
        self.setEnabled(enabled)
        if text:
            self.setText(text)
        elif enabled and not self.is_processing:
            self.setText(self.default_text)


class ActionButton(QPushButton):
    """일반 액션 버튼"""

    def __init__(self, text: str, tooltip: str = ""):
        super().__init__(text)
        self.setStyleSheet(StyleSheet.button())
        if tooltip:
            self.setToolTip(tooltip)


class ClearButton(QPushButton):
    """초기화 버튼"""

    def __init__(self):
        super().__init__("🗑️ 초기화")
        self.setStyleSheet(self._get_clear_button_style())
        self.setToolTip("모든 입력 필드를 초기화합니다")

    def _get_clear_button_style(self) -> str:
        """초기화 버튼 전용 스타일"""
        return """
            QPushButton {
                background-color: #e74c3c;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
            QPushButton:pressed {
                background-color: #a93226;
            }
        """