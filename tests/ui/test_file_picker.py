"""Tests for the fakeable native file-picker abstraction."""

from __future__ import annotations

from pathlib import Path

from soft_actuator_testing.ui.widgets.file_picker import FakeFilePicker, FileFilter


def test_fake_file_picker_returns_queued_results_in_order() -> None:
    picker = FakeFilePicker(queued_results=[Path("/demo/a.csv"), None])
    assert picker.get_open_file(caption="Open") == Path("/demo/a.csv")
    assert picker.get_save_file(caption="Save") is None


def test_fake_file_picker_never_opens_a_real_dialog_and_records_calls() -> None:
    picker = FakeFilePicker(queued_results=[Path("/demo/dir")])
    filters = (FileFilter("CSV files", ("*.csv",)),)
    result = picker.get_existing_directory(caption="Choose a folder")
    assert result == Path("/demo/dir")

    picker.get_open_file(caption="Open video", filters=filters)
    assert [call.method for call in picker.calls] == ["get_existing_directory", "get_open_file"]
    assert picker.calls[1].filters == filters


def test_missing_queued_result_defaults_to_cancelled() -> None:
    picker = FakeFilePicker()
    assert picker.get_open_file(caption="Open") is None


def test_fake_file_picker_returns_queued_multi_results_in_order() -> None:
    picker = FakeFilePicker(
        queued_multi_results=[(Path("/demo/a.json"), Path("/demo/b.json")), ()]
    )
    assert picker.get_open_files(caption="Open individual artifact files") == (
        Path("/demo/a.json"),
        Path("/demo/b.json"),
    )
    assert picker.get_open_files(caption="Open individual artifact files") == ()


def test_missing_queued_multi_result_defaults_to_cancelled() -> None:
    picker = FakeFilePicker()
    assert picker.get_open_files(caption="Open individual artifact files") == ()


def test_fake_file_picker_records_multi_select_calls_separately_from_single_select() -> None:
    picker = FakeFilePicker(queued_multi_results=[(Path("/demo/a.json"),)])
    filters = (FileFilter("Versioned artifact", ("*.json",)),)
    picker.get_open_files(caption="Open individual artifact files", filters=filters)
    assert [call.method for call in picker.calls] == ["get_open_files"]
    assert picker.calls[0].filters == filters
    # The single-file queue is independent and unaffected by multi-select calls.
    assert picker.queued_results == []


def test_file_filter_renders_qt_style_filter_string() -> None:
    filt = FileFilter("Video files", ("*.mp4", "*.mkv"))
    assert filt.to_qt_filter() == "Video files (*.mp4 *.mkv)"
