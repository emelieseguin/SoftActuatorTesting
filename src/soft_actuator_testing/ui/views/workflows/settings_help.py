"""Settings, profiles, and help workflow page."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QVBoxLayout

from soft_actuator_testing.application.presentation import ApplicationSnapshot, ApplySettings
from soft_actuator_testing.ui.views.base import PageScenario, WorkflowPage
from soft_actuator_testing.ui.widgets import AccessibleButton


class SettingsProfilesHelpPage(WorkflowPage):
    def __init__(self, **kwargs) -> None:
        super().__init__("Settings / Profiles / Help", **kwargs)
        settings = self.section("Settings and profiles")
        form = QFormLayout(settings)
        self.profile = QComboBox(settings)
        self.profile.setObjectName("demo-profile")
        self.profile.setAccessibleName("Demo profile")
        self.profile.addItems(["Operator", "Researcher", "Training"])
        self.compact_mode = QCheckBox("Use compact demo density", settings)
        self.compact_mode.setObjectName("compact-demo-density")
        self.compact_mode.setAccessibleName("Use compact demo density")
        self.save_settings_button = AccessibleButton("Apply demo settings")
        self.save_settings_button.setObjectName("apply-demo-settings")
        self.save_settings_button.clicked.connect(self.apply_settings)
        self.settings_result = QLabel(settings)
        self.settings_result.setObjectName("settings-result")
        self.settings_result.setAccessibleName("Settings result")
        form.addRow("Profile", self.profile)
        form.addRow(self.compact_mode)
        form.addRow(self.save_settings_button)
        form.addRow("Status", self.settings_result)
        help_group = self.section("Help")
        help_layout = QVBoxLayout(help_group)
        help_text = QLabel(
            "Demo mode uses deterministic fake services as adapters behind the application presenter. "
            "The shell retains fixed Global Stop and run/fault chrome.",
            help_group,
        )
        help_text.setObjectName("help-text")
        help_text.setWordWrap(True)
        help_text.setAccessibleName("Prototype help")
        help_layout.addWidget(help_text)
        self.layout.addStretch(1)
        self._bind_presenter()

    def render_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        settings = snapshot.settings
        self.profile.setCurrentText(settings.profile)
        self.compact_mode.setChecked(settings.compact_mode)
        self.settings_result.setText(settings.result)

    def apply_settings(self) -> None:
        self.dispatch(ApplySettings(self.profile.currentText(), self.compact_mode.isChecked()))
        self.set_scenario(PageScenario.COMPLETED)
