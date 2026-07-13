"""Native file-picker abstraction that can be faked in tests.

No test may open a real native dialog (the test plan requires the default
suite to run headless with no hardware/native-UI dependence), so every
screen that needs Open/Save/"choose a folder" behavior depends on the
:class:`FilePicker` protocol rather than calling ``QFileDialog`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FileFilter:
    """A named file-type filter, e.g. ``FileFilter("Video files", ("*.mp4", "*.mkv"))``."""

    label: str
    patterns: tuple[str, ...]

    def to_qt_filter(self) -> str:
        return f"{self.label} ({' '.join(self.patterns)})"


@runtime_checkable
class FilePicker(Protocol):
    """Native-dialog boundary; UI code depends on this, never ``QFileDialog`` directly."""

    def get_open_file(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> Path | None: ...

    def get_open_files(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> tuple[Path, ...]: ...

    def get_save_file(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> Path | None: ...

    def get_existing_directory(
        self,
        *,
        caption: str,
        directory: Path | None = None,
    ) -> Path | None: ...


class QtFilePicker:
    """Real ``QFileDialog``-backed picker; constructed only outside of tests."""

    def __init__(self, parent=None) -> None:
        self._parent = parent

    def get_open_file(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> Path | None:
        from PySide6.QtWidgets import QFileDialog

        filter_string = ";;".join(f.to_qt_filter() for f in filters)
        path, _ = QFileDialog.getOpenFileName(
            self._parent, caption, str(directory or ""), filter_string
        )
        return Path(path) if path else None

    def get_open_files(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> tuple[Path, ...]:
        from PySide6.QtWidgets import QFileDialog

        filter_string = ";;".join(f.to_qt_filter() for f in filters)
        paths, _ = QFileDialog.getOpenFileNames(
            self._parent, caption, str(directory or ""), filter_string
        )
        return tuple(Path(path) for path in paths if path)

    def get_save_file(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> Path | None:
        from PySide6.QtWidgets import QFileDialog

        filter_string = ";;".join(f.to_qt_filter() for f in filters)
        path, _ = QFileDialog.getSaveFileName(
            self._parent, caption, str(directory or ""), filter_string
        )
        return Path(path) if path else None

    def get_existing_directory(
        self,
        *,
        caption: str,
        directory: Path | None = None,
    ) -> Path | None:
        from PySide6.QtWidgets import QFileDialog

        path = QFileDialog.getExistingDirectory(self._parent, caption, str(directory or ""))
        return Path(path) if path else None


@dataclass
class RecordedFilePickerCall:
    """One recorded call, useful for asserting UI-triggered picker invocations."""

    method: str
    caption: str
    directory: Path | None
    filters: tuple[FileFilter, ...] = ()


@dataclass
class FakeFilePicker:
    """A deterministic, non-dialog-opening double for tests.

    ``queued_results`` is consumed in FIFO order across
    ``get_open_file``/``get_save_file``/``get_existing_directory`` so a test
    scenario can script a sequence of picker outcomes (including ``None`` for
    "user cancelled"). ``queued_multi_results`` is a separate FIFO queue used
    only by ``get_open_files`` (empty tuple means "user selected nothing /
    cancelled").
    """

    queued_results: list[Path | None] = field(default_factory=list)
    queued_multi_results: list[tuple[Path, ...]] = field(default_factory=list)
    calls: list[RecordedFilePickerCall] = field(default_factory=list, init=False)

    def _next_result(self) -> Path | None:
        if not self.queued_results:
            return None
        return self.queued_results.pop(0)

    def _next_multi_result(self) -> tuple[Path, ...]:
        if not self.queued_multi_results:
            return ()
        return self.queued_multi_results.pop(0)

    def get_open_file(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> Path | None:
        self.calls.append(RecordedFilePickerCall("get_open_file", caption, directory, filters))
        return self._next_result()

    def get_open_files(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> tuple[Path, ...]:
        self.calls.append(RecordedFilePickerCall("get_open_files", caption, directory, filters))
        return self._next_multi_result()

    def get_save_file(
        self,
        *,
        caption: str,
        directory: Path | None = None,
        filters: tuple[FileFilter, ...] = (),
    ) -> Path | None:
        self.calls.append(RecordedFilePickerCall("get_save_file", caption, directory, filters))
        return self._next_result()

    def get_existing_directory(
        self,
        *,
        caption: str,
        directory: Path | None = None,
    ) -> Path | None:
        self.calls.append(RecordedFilePickerCall("get_existing_directory", caption, directory))
        return self._next_result()
