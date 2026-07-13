from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from soft_actuator_testing.infrastructure.ffmpeg import (
    CameraInputProfile,
    CaptureStoragePolicy,
    FfmpegProbeError,
    FfmpegTools,
    FfmpegUnavailableError,
    ProgressParser,
    StreamCopyEvidence,
    build_camera_input_arguments,
    build_capture_command,
    build_device_list_command,
    build_profile_list_command,
    estimate_storage,
    parse_negotiated_profile,
    parse_camera_modes,
    probe_capabilities,
    select_runtime_encoder,
    verify_video,
)


def _tools(root: Path) -> FfmpegTools:
    ffmpeg = root / "ffmpeg"
    ffprobe = root / "ffprobe"
    ffmpeg.write_text("", encoding="utf-8")
    ffprobe.write_text("", encoding="utf-8")
    return FfmpegTools(ffmpeg, ffprobe)


def _completed(
    command,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_package_import_and_discovery_fail_cleanly_without_ffmpeg(monkeypatch) -> None:
    monkeypatch.delenv("SOFT_ACTUATOR_FFMPEG", raising=False)
    with pytest.raises(FfmpegUnavailableError, match="PATH"):
        FfmpegTools.discover(which=lambda name: None)


def test_discovery_finds_matching_pair_from_configured_directory(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    discovered = FfmpegTools.discover(ffmpeg=tmp_path, platform="posix")
    assert discovered == FfmpegTools(tools.ffmpeg.resolve(), tools.ffprobe.resolve())


def test_capability_probe_records_version_build_devices_and_encoders(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    def runner(command, **kwargs):
        del kwargs
        option = command[-1]
        output = {
            "-version": "ffmpeg version 7.1\nconfiguration: --enable-libx264",
            "-buildconf": "configuration: --enable-libx264",
            "-devices": " D  v4l2           Video4Linux2",
            "-encoders": " V....D h264_nvenc NVIDIA NVENC H.264 encoder",
            "-hwaccels": "Hardware acceleration methods:\ncuda",
        }[option]
        return _completed(command, stdout=output)

    capabilities = probe_capabilities(tools, runner=runner)
    assert capabilities.version_line == "ffmpeg version 7.1"
    assert capabilities.has_device("v4l2")
    assert capabilities.has_encoder("h264_nvenc")
    assert "libx264" in capabilities.build_configuration


def test_capability_probe_rejects_failed_or_unrecognized_version(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    with pytest.raises(FfmpegProbeError, match="version probe failed"):
        probe_capabilities(
            tools,
            runner=lambda command, **kwargs: _completed(command, 1, stderr="broken"),
        )


def test_windows_directshow_commands_request_exact_target_profile(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    profile = CameraInputProfile(pixel_format="mjpeg")
    assert build_device_list_command(tools, platform="win32")[-5:] == [
        "-list_devices",
        "true",
        "-f",
        "dshow",
        "-i",
        "dummy",
    ][-5:]
    profiles = build_profile_list_command(tools, "USB Camera", platform="win32")
    assert profiles[-1] == "video=USB Camera"
    arguments = build_camera_input_arguments("USB Camera", profile, platform="win32")
    assert arguments[arguments.index("-video_size") + 1] == "3840x2160"
    assert arguments[arguments.index("-framerate") + 1] == "60"
    assert arguments[-1] == "video=USB Camera"


def test_linux_v4l2_commands_request_exact_target_profile(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    profile = CameraInputProfile(pixel_format="mjpeg")
    profiles = build_profile_list_command(tools, "/dev/video4", platform="linux")
    assert profiles[-1] == "/dev/video4"
    assert "v4l2" in profiles
    arguments = build_camera_input_arguments("/dev/video4", profile, platform="linux")
    assert arguments[arguments.index("-input_format") + 1] == "mjpeg"
    assert arguments[arguments.index("-video_size") + 1] == "3840x2160"
    assert arguments[arguments.index("-framerate") + 1] == "60"


def test_non_target_input_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="exactly 3840x2160@60"):
        build_camera_input_arguments(
            "/dev/video0",
            CameraInputProfile(width=1920, height=1080, fps=30),
            platform="linux",
        )


def test_runtime_encoder_uses_verified_copy_then_actual_fallback_order(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    copy = select_runtime_encoder(
        tools,
        stream_copy=StreamCopyEvidence("h264", True, True),
        runner=lambda *args, **kwargs: pytest.fail("eligible copy should not encode"),
    )
    assert copy.stream_copy

    attempts: list[str] = []

    def runner(command, **kwargs):
        del kwargs
        encoder = command[command.index("-c:v") + 1]
        attempts.append(encoder)
        return _completed(command, 0 if encoder == "h264_vaapi" else 1, stderr="not usable")

    selected = select_runtime_encoder(tools, runner=runner)
    assert selected.name == "h264_vaapi"
    assert attempts == ["h264_nvenc", "h264_qsv", "h264_vaapi"]
    assert selected.global_arguments == ("-vaapi_device", "/dev/dri/renderD128")
    assert selected.record_filter == "format=nv12,hwupload"


def test_runtime_encoder_raises_after_software_encode_failure(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    with pytest.raises(FfmpegProbeError, match="libx264"):
        select_runtime_encoder(
            tools,
            runner=lambda command, **kwargs: _completed(command, 1, stderr="failed"),
        )


def test_capture_command_has_one_input_record_output_and_drained_preview(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    encoder = select_runtime_encoder(
        tools,
        runner=lambda command, **kwargs: _completed(
            command,
            0 if command[command.index("-c:v") + 1] == "libx264" else 1,
            stderr="unavailable",
        ),
    )
    partial = tmp_path / "video.partial.mkv"
    command = build_capture_command(
        tools,
        input_arguments=["-f", "v4l2", "-i", "/dev/video0"],
        encoder=encoder,
        partial_path=partial,
    )
    assert command.count("-i") == 1
    assert str(partial) in command
    assert "split=2" in " ".join(command)
    assert "pipe:1" in command
    assert command[-2:] == ["-progress", "pipe:2"]


def test_progress_and_negotiated_format_parsing_tolerate_noise() -> None:
    parser = ProgressParser()
    assert not parser.feed("[video4linux2] informational log")
    parser.feed("frame=60")
    parser.feed("fps=59.9")
    parser.feed("speed=1.01x")
    parser.feed("out_time_us=1000000")
    parser.feed("dup_frames=2")
    parser.feed("drop_frames=3")
    parser.feed("frame=not-a-number")
    assert parser.value.frame == 60
    assert parser.value.malformed_lines == 1
    assert parser.value.dropped_frames == 3

    profile = parse_negotiated_profile(
        "Stream #0:0: Video: mjpeg (Baseline), yuvj422p(pc), "
        "3840x2160, 60 fps, 60 tbr"
    )
    assert profile is not None
    profile.verify(expected_pixel_format="mjpeg")
    with pytest.raises(ValueError, match="required 3840x2160@60"):
        parse_negotiated_profile(
            "Stream #0:0: Video: mjpeg, yuvj422p, 1920x1080, 30 fps"
        ).verify()


def test_storage_estimate_exposes_free_space_hook(tmp_path: Path) -> None:
    estimate = estimate_storage(
        tmp_path,
        duration_seconds=10,
        bytes_per_second=20,
        reserve_bytes=100,
        disk_usage=lambda path: shutil._ntuple_diskusage(1_000, 600, 400),
    )
    assert estimate.recording_bytes == 200
    assert estimate.required_free_bytes == 300
    assert estimate.fits


def test_capture_storage_policy_uses_nonzero_conservative_default_and_refuses_full_disk(
    tmp_path: Path,
) -> None:
    policy = CaptureStoragePolicy(
        default_duration_seconds=30,
        bytes_per_second=20,
        reserve_bytes=100,
    )
    estimate = policy.estimate(
        tmp_path / "new-run",
        None,
        disk_usage=lambda path: shutil._ntuple_diskusage(1_000, 601, 599),
    )

    assert estimate.duration_seconds == 30
    assert estimate.recording_bytes == 600
    assert estimate.required_free_bytes == 700
    assert not estimate.fits
    with pytest.raises(OSError, match="Insufficient free storage.*need 700"):
        policy.preflight(
            tmp_path / "new-run",
            None,
            disk_usage=lambda path: shutil._ntuple_diskusage(1_000, 601, 599),
        )


def test_mode_parser_retains_only_explicit_format_dimension_rate_evidence() -> None:
    modes = parse_camera_modes(
        """
        [dshow]   vcodec=mjpeg  min s=3840x2160 fps=30 max s=3840x2160 fps=60
        Pixel Format: 'YUYV'
            Size: Discrete 1920x1080
                Interval: Discrete 0.033s (30.000 fps)
        Pixel Format: 'MJPG'
            Size: Discrete 3840x2160
                Interval: Discrete 0.016s (60.000 fps)
        """
    )

    assert {(mode.width, mode.height, mode.fps, mode.pixel_format) for mode in modes} == {
        (3840, 2160, 30.0, "mjpeg"),
        (3840, 2160, 60.0, "mjpeg"),
        (1920, 1080, 30.0, "yuyv"),
        (3840, 2160, 60.0, "mjpg"),
    }


def test_ffprobe_verification_requires_a_readable_video_stream(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    video = tmp_path / "video.partial.mkv"
    video.write_bytes(b"matroska")
    payload = {
        "streams": [
            {
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "60/1",
                "nb_read_frames": "120",
            }
        ],
        "format": {"duration": "2.0", "size": str(video.stat().st_size)},
    }
    verification = verify_video(
        tools,
        video,
        runner=lambda command, **kwargs: _completed(command, stdout=json.dumps(payload)),
    )
    assert verification.readable
    assert verification.frames == 120

    invalid = verify_video(
        tools,
        video,
        runner=lambda command, **kwargs: _completed(command, stdout="{bad json"),
    )
    assert not invalid.readable
    assert "ffprobe response" in invalid.error
