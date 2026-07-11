from __future__ import annotations

import subprocess
from pathlib import Path
from time import monotonic, sleep

import pytest

from soft_actuator_testing.application.camera_capture import CaptureError, CapturePhase
from soft_actuator_testing.infrastructure.camera import (
    FakeProcessFactory,
    FfmpegCameraDeviceSource,
    FfmpegCaptureBackend,
    ScriptedProcess,
)
from soft_actuator_testing.infrastructure.ffmpeg import (
    CameraInputProfile,
    EncoderSelection,
    FfmpegTools,
    VideoVerification,
)


def _tools(root: Path) -> FfmpegTools:
    ffmpeg = root / "ffmpeg"
    ffprobe = root / "ffprobe"
    ffmpeg.write_text("", encoding="utf-8")
    ffprobe.write_text("", encoding="utf-8")
    return FfmpegTools(ffmpeg, ffprobe)


def _stderr(*, width: int = 3840, malformed: bool = False) -> bytes:
    lines = [
        f"Stream #0:0: Video: mjpeg (Baseline), yuvj422p(pc), {width}x2160, 60 fps",
        "frame=1",
        "out_time_us=1000",
        "total_size=6",
        "progress=continue",
        "frame=2",
        "out_time_us=2000",
        "total_size=12",
        "drop_frames=0",
        "dup_frames=0",
        "progress=continue",
    ]
    if malformed:
        lines.append("frame=broken")
    return ("\n".join(lines) + "\n").encode()


def _realistic_multistream_stderr() -> bytes:
    return (
        "Input #0, video4linux2,v4l2, from '/dev/video0':\n"
        "  Stream #0:0: Video: mjpeg (Baseline), yuvj422p(pc), 3840x2160, 60 fps\n"
        "Output #0, matroska, to 'video.partial.mkv':\n"
        "  Stream #0:0: Video: h264, yuv420p, 3840x2160, 60 fps\n"
        "Output #1, rawvideo, to 'pipe:1':\n"
        "  Stream #1:0: Video: rawvideo, rgb24, 2x1, 10 fps\n"
        "frame=1\nout_time_us=1000\ntotal_size=6\nprogress=continue\n"
        "frame=2\nout_time_us=2000\ntotal_size=12\nprogress=continue\n"
    ).encode()


def _verification(readable: bool = True) -> VideoVerification:
    return VideoVerification(
        readable=readable,
        codec="h264" if readable else "",
        width=3840 if readable else 0,
        height=2160 if readable else 0,
        frames=2 if readable else 0,
        duration_seconds=0.03 if readable else 0,
        size_bytes=12 if readable else 0,
        error="" if readable else "synthetic invalid partial",
    )


def _backend(
    tmp_path: Path,
    factory: FakeProcessFactory,
    *,
    readable: bool = True,
    health_observer=None,
    throughput_observer=None,
    graceful_timeout: float = 0.02,
    interrupt_timeout: float = 0.02,
) -> FfmpegCaptureBackend:
    return FfmpegCaptureBackend(
        _tools(tmp_path),
        platform="linux",
        input_profile=CameraInputProfile(pixel_format="mjpeg"),
        encoder=EncoderSelection(
            name="libx264",
            output_arguments=("-c:v", "libx264", "-preset", "ultrafast"),
        ),
        process_factory=factory,
        verifier=lambda path: _verification(readable),
        preview_width=2,
        preview_height=1,
        preview_fps=10,
        graceful_timeout=graceful_timeout,
        interrupt_timeout=interrupt_timeout,
        poll_interval=0.001,
        health_observer=health_observer,
        throughput_observer=throughput_observer,
    )


def _factory(
    output_directory: Path,
    *,
    frames: int = 1,
    stderr: bytes | None = None,
    quit_exits: bool = True,
    interrupt_exits: bool = True,
) -> FakeProcessFactory:
    def supply(command):
        partial = next(Path(item) for item in command if str(item).endswith("video.partial.mkv"))
        partial.write_bytes(b"partial-video")
        return ScriptedProcess(
            stdout=b"\x10\x20\x30\x40\x50\x60" * frames,
            stderr=_stderr() if stderr is None else stderr,
            quit_exits=quit_exits,
            interrupt_exits=interrupt_exits,
        )

    return FakeProcessFactory(supply)


def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        sleep(0.002)
    assert predicate()


def test_real_device_source_enumerates_without_opening_devices(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    result = subprocess.CompletedProcess(
        [],
        1,
        "",
        '[dshow @ 000] "USB Camera" (video)\n[dshow @ 000] "IR Camera" (video)\n',
    )
    windows = FfmpegCameraDeviceSource(
        tools,
        platform="win32",
        runner=lambda *args, **kwargs: result,
    )
    assert [device.identifier for device in windows.devices()] == ["USB Camera", "IR Camera"]
    assert windows.profile_command("USB Camera")[-1] == "video=USB Camera"

    device_root = tmp_path / "dev"
    device_root.mkdir()
    (device_root / "video0").write_bytes(b"")
    linux = FfmpegCameraDeviceSource(
        tools,
        platform="linux",
        linux_device_directory=device_root,
    )
    assert [device.name for device in linux.devices()] == ["video0"]
    assert linux.profile_command(str(device_root / "video0"))[-1].endswith("video0")


def test_slow_preview_consumer_never_blocks_recording_and_replaces_stale_frames(
    tmp_path: Path,
) -> None:
    throughputs: list[float] = []
    factory = _factory(tmp_path, frames=40)
    backend = _backend(
        tmp_path,
        factory,
        throughput_observer=lambda value, health: throughputs.append(value),
    )
    run = tmp_path / "run"
    backend.start(run, "/dev/video0", readiness_timeout=0.5)
    _wait_until(lambda: backend.frame_channel.stats.produced == 40)
    stats = backend.frame_channel.stats
    assert stats.replaced_stale == 39
    assert backend.health.frame == 2
    assert throughputs

    latest = backend.frame_channel.consume_latest()
    assert latest is not None and latest.index == 39
    result = backend.stop()
    assert result.readable and result.video_path == run / "video.mkv"
    assert not (run / "video.partial.mkv").exists()


def test_startup_rejects_wrong_negotiated_profile_and_preserves_partial(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path, stderr=_stderr(width=1920))
    backend = _backend(tmp_path, factory, readable=False)
    run = tmp_path / "run"
    with pytest.raises(CaptureError, match="required 3840x2160@60"):
        backend.start(run, "/dev/video0", readiness_timeout=0.2)
    assert (run / "video.partial.mkv").exists()
    assert not (run / "video.mkv").exists()
    assert backend.result is not None
    assert backend.result.health.phase is CapturePhase.FAULT


def test_input_negotiation_is_not_replaced_by_record_or_preview_outputs(tmp_path: Path) -> None:
    backend = _backend(
        tmp_path,
        _factory(tmp_path, stderr=_realistic_multistream_stderr()),
    )

    backend.start(tmp_path / "run", "/dev/video0", readiness_timeout=0.5)

    profile = backend.health.negotiated_profile
    assert profile is not None
    assert (profile.width, profile.height, profile.fps) == (3840, 2160, 60)
    backend.stop()


def test_readable_startup_rejection_is_not_promoted_to_completed_video(tmp_path: Path) -> None:
    backend = _backend(
        tmp_path,
        _factory(tmp_path, stderr=_stderr(width=1920)),
        readable=True,
    )
    run = tmp_path / "run"

    with pytest.raises(CaptureError, match="required 3840x2160@60"):
        backend.start(run, "/dev/video0", readiness_timeout=0.2)

    assert (run / "video.partial.mkv").is_file()
    assert not (run / "video.mkv").exists()
    assert backend.result is not None and backend.result.video_path is None


def test_startup_requires_preview_frame_and_file_progress(tmp_path: Path) -> None:
    def supply(command):
        return ScriptedProcess(stdout=b"", stderr=_stderr())

    backend = _backend(tmp_path, FakeProcessFactory(supply), readable=False)
    with pytest.raises(CaptureError, match="startup timed out"):
        backend.start(tmp_path / "run", "/dev/video0", readiness_timeout=0.03)


def test_malformed_progress_is_reported_without_killing_capture(tmp_path: Path) -> None:
    backend = _backend(
        tmp_path,
        _factory(tmp_path, stderr=_stderr(malformed=True)),
    )
    backend.start(tmp_path / "run", "/dev/video0", readiness_timeout=0.5)
    _wait_until(lambda: backend.health.malformed_progress_lines == 1)
    assert backend.health.ready
    backend.stop()


def test_invalid_ffprobe_result_keeps_partial_for_diagnostics(tmp_path: Path) -> None:
    backend = _backend(tmp_path, _factory(tmp_path), readable=False)
    run = tmp_path / "run"
    backend.start(run, "/dev/video0", readiness_timeout=0.5)
    result = backend.stop()
    assert not result.readable
    assert result.video_path is None
    assert result.partial_path.exists()
    assert "invalid partial" in result.error


def test_stop_is_idempotent_and_existing_partial_is_preserved(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    previous = run / "video.partial.mkv"
    previous.write_bytes(b"old diagnostic")
    backend = _backend(tmp_path, _factory(tmp_path))
    backend.start(run, "/dev/video0", readiness_timeout=0.5)
    preserved = list(run.glob("video.partial.*.mkv"))
    assert len(preserved) == 1 and preserved[0].read_bytes() == b"old diagnostic"
    first = backend.stop()
    second = backend.stop()
    assert first is second
    assert len(backend.command) > 0


def test_timeout_escalates_to_interrupt_then_kill(tmp_path: Path) -> None:
    factory = _factory(
        tmp_path,
        quit_exits=False,
        interrupt_exits=False,
    )
    backend = _backend(tmp_path, factory)
    backend.start(tmp_path / "run", "/dev/video0", readiness_timeout=0.5)
    result = backend.stop()
    process = factory.processes[0]
    assert process.signals
    assert process.killed
    assert not result.clean


def test_disconnect_and_close_use_the_same_finalizer(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    backend = _backend(tmp_path, factory)
    backend.start(tmp_path / "disconnect", "/dev/video0", readiness_timeout=0.5)
    factory.processes[0].finish(17)
    _wait_until(lambda: backend.result is not None)
    disconnected = backend.result
    assert disconnected is not None
    assert disconnected.completion_reason == "disconnect"
    assert disconnected.health.phase is CapturePhase.FAULT

    close_factory = _factory(tmp_path)
    close_backend = _backend(tmp_path, close_factory)
    close_backend.start(tmp_path / "close", "/dev/video0", readiness_timeout=0.5)
    closed = close_backend.close()
    assert closed is not None
    assert closed.completion_reason == "close"


def test_only_one_active_owner_is_allowed(tmp_path: Path) -> None:
    backend = _backend(tmp_path, _factory(tmp_path))
    backend.start(tmp_path / "run", "/dev/video0", readiness_timeout=0.5)
    with pytest.raises(CaptureError, match="active owner"):
        backend.start(tmp_path / "other", "/dev/video0", readiness_timeout=0.5)
    backend.stop()
