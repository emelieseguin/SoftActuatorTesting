"""Accessible notification banners and a deterministic notification center."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from soft_actuator_testing.ui.themes.qt_bridge import to_qcolor, to_qfont
from soft_actuator_testing.ui.themes.tokens import SemanticState, Theme
from soft_actuator_testing.ui.widgets.controls import AccessibleButton


@dataclass(frozen=True)
class Notification:
    """An immutable notification record; the UI never mutates one in place."""

    message: str
    severity: SemanticState = SemanticState.INFO
    notification_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dismissible: bool = True


class NotificationBanner(QFrame):
    """A single themed, accessible banner for one :class:`Notification`.

    Uses an Qt "accessible alert" role so screen readers announce the
    message as it appears, and shows a severity glyph/label so meaning never
    depends on color alone.
    """

    dismissed = Signal(str)

    def __init__(self, notification: Notification, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._notification = notification
        self._theme: Theme | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)

        self._glyph_label = QLabel(self)
        self._message_label = QLabel(notification.message, self)
        self._message_label.setWordWrap(True)
        self._message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout.addWidget(self._glyph_label)
        layout.addWidget(self._message_label, 1)

        self._dismiss_button: AccessibleButton | None = None
        if notification.dismissible:
            self._dismiss_button = AccessibleButton(
                "Dismiss",
                accessible_description=f"Dismiss notification: {notification.message}",
                variant="secondary",
                parent=self,
            )
            self._dismiss_button.clicked.connect(
                lambda: self.dismissed.emit(notification.notification_id)
            )
            layout.addWidget(self._dismiss_button)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAccessibleName(f"{notification.severity.value} notification")
        self.setAccessibleDescription(notification.message)

    @property
    def notification(self) -> Notification:
        return self._notification

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        style = theme.state_style(self._notification.severity)
        self._glyph_label.setText(style.glyph)
        self._message_label.setFont(to_qfont(theme.typography.body))
        palette = self.palette()
        palette.setColor(self.foregroundRole(), to_qcolor(style.color))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        if self._dismiss_button is not None:
            self._dismiss_button.apply_theme(theme)


class NotificationCenter(QWidget):
    """Holds an ordered, deterministic stack of active notification banners."""

    notification_added = Signal(str)
    notification_removed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._layout.addStretch(1)
        self._banners: dict[str, NotificationBanner] = {}
        self._theme: Theme | None = None
        self.setAccessibleName("Notifications")

    @property
    def active_ids(self) -> tuple[str, ...]:
        """IDs in display order (oldest first), for deterministic assertions."""

        return tuple(self._banners.keys())

    def notify(self, notification: Notification) -> NotificationBanner:
        banner = NotificationBanner(notification, self)
        banner.dismissed.connect(self.dismiss)
        if self._theme is not None:
            banner.apply_theme(self._theme)
        # Insert before the trailing stretch so new banners append in order.
        self._layout.insertWidget(self._layout.count() - 1, banner)
        self._banners[notification.notification_id] = banner
        self.notification_added.emit(notification.notification_id)
        return banner

    def dismiss(self, notification_id: str) -> None:
        banner = self._banners.pop(notification_id, None)
        if banner is None:
            return
        self._layout.removeWidget(banner)
        banner.deleteLater()
        self.notification_removed.emit(notification_id)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        for banner in self._banners.values():
            banner.apply_theme(theme)


NotificationHandler = Callable[[Notification], None]
