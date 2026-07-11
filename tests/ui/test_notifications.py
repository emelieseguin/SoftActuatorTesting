"""Tests for the accessible notification banner/notification-center pattern."""

from __future__ import annotations

from soft_actuator_testing.ui.themes.tokens import DARK_THEME, SemanticState
from soft_actuator_testing.ui.widgets.notifications import (
    Notification,
    NotificationBanner,
    NotificationCenter,
)


def test_notification_banner_accessible_description_is_the_message(qtbot) -> None:
    notification = Notification("Camera disconnected", severity=SemanticState.ERROR)
    banner = NotificationBanner(notification)
    banner.apply_theme(DARK_THEME)
    qtbot.addWidget(banner)
    assert banner.accessibleDescription() == "Camera disconnected"
    assert banner.accessibleName() == "error notification"


def test_dismissible_banner_emits_dismissed_with_notification_id(qtbot) -> None:
    notification = Notification("Saved calibration", severity=SemanticState.SUCCESS)
    banner = NotificationBanner(notification)
    qtbot.addWidget(banner)

    with qtbot.waitSignal(banner.dismissed, timeout=1000) as blocker:
        banner._dismiss_button.click()
    assert blocker.args == [notification.notification_id]


def test_non_dismissible_banner_has_no_dismiss_button(qtbot) -> None:
    notification = Notification("Demo mode active", dismissible=False)
    banner = NotificationBanner(notification)
    qtbot.addWidget(banner)
    assert banner._dismiss_button is None


def test_notification_center_tracks_deterministic_order_and_dismiss(qtbot) -> None:
    center = NotificationCenter()
    center.apply_theme(DARK_THEME)
    qtbot.addWidget(center)

    first = Notification("First")
    second = Notification("Second")
    center.notify(first)
    center.notify(second)

    assert center.active_ids == (first.notification_id, second.notification_id)

    with qtbot.waitSignal(center.notification_removed, timeout=1000):
        center.dismiss(first.notification_id)

    assert center.active_ids == (second.notification_id,)


def test_notification_center_ignores_dismiss_of_unknown_id(qtbot) -> None:
    center = NotificationCenter()
    qtbot.addWidget(center)
    center.dismiss("does-not-exist")  # must not raise
    assert center.active_ids == ()
