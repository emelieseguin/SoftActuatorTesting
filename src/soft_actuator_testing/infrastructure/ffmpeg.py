"""Cross-platform FFmpeg discovery, probing, commands, and telemetry parsing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from soft_actuator_testing.application.camera_capture import (
    CaptureTargetProfile,
    NegotiatedCaptureProfile,
    TARGET_4K60,
)


class FfmpegUnavailableError(RuntimeError):
    pass


class FfmpegProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class FfmpegTools:
    ffmpeg: Path
    ffprobe: Path

    @classmethod
    def discover(
        cls,
        *,
        ffmpeg: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        platform: str | None = None,
    ) -> FfmpegTools:
        env = os.environ if environment is None else environment
        native_platform = os.name if platform is None else platform
        executable_name = "ffmpeg.exe" if native_platform == "nt" else "ffmpeg"
        probe_name = "ffprobe.exe" if native_platform == "nt" else "ffprobe"
        configured = ffmpeg or env.get("SOFT_ACTUATOR_FFMPEG")
        ffmpeg_path = Path(configured) if configured else None
        if ffmpeg_path is not None and ffmpeg_path.is_dir():
            ffmpeg_path /= executable_name
        if ffmpeg_path is None:
            located = which(executable_name) or (which("ffmpeg") if executable_name != "ffmpeg" else None)
            ffmpeg_path = Path(located) if located else None
        if ffmpeg_path is None or not ffmpeg_path.is_file():
            raise FfmpegUnavailableError(
                "FFmpeg was not found. Install a supported ffmpeg/ffprobe pair, "
                "add it to PATH, or set SOFT_ACTUATOR_FFMPEG."
            )

        sibling_probe = ffmpeg_path.with_name(probe_name)
        if sibling_probe.is_file():
            ffprobe_path = sibling_probe
        else:
            located_probe = which(probe_name) or (which("ffprobe") if probe_name != "ffprobe" else None)
            ffprobe_path = Path(located_probe) if located_probe else None
        if ffprobe_path is None or not ffprobe_path.is_file():
            raise FfmpegUnavailableError(
                f"FFmpeg was found at {ffmpeg_path}, but the matching ffprobe executable is missing."
            )
        return cls(ffmpeg=ffmpeg_path.resolve(), ffprobe=ffprobe_path.resolve())


@dataclass(frozen=True)
class FfmpegCapabilities:
    version_line: str
    version_text: str
    build_configuration: str
    devices: str
    encoders: str
    hardware_accelerators: str

    def has_encoder(self, encoder: str) -> bool:
        return bool(re.search(rf"^\s*[A-Z.]+\s+{re.escape(encoder)}\s", self.encoders, re.MULTILINE))

    def has_device(self, device: str) -> bool:
        return bool(re.search(rf"\b{re.escape(device)}\b", self.devices))


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _run_text(command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - project-owned executable and argument list
        list(command),
        check=False,
        capture_output=True,
        text=True,
        **kwargs,
    )


def probe_capabilities(
    tools: FfmpegTools,
    *,
    runner: RunCommand = _run_text,
    timeout: float = 10.0,
) -> FfmpegCapabilities:
    probes = {
        "version": [str(tools.ffmpeg), "-hide_banner", "-version"],
        "build": [str(tools.ffmpeg), "-hide_banner", "-buildconf"],
        "devices": [str(tools.ffmpeg), "-hide_banner", "-devices"],
        "encoders": [str(tools.ffmpeg), "-hide_banner", "-encoders"],
        "hardware": [str(tools.ffmpeg), "-hide_banner", "-hwaccels"],
    }
    output: dict[str, str] = {}
    for name, command in probes.items():
        result = runner(command, timeout=timeout)
        text = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if result.returncode != 0:
            raise FfmpegProbeError(f"FFmpeg {name} probe failed: {text.strip()}")
        output[name] = text
    version_line = next(
        (line.strip() for line in output["version"].splitlines() if line.strip()),
        "",
    )
    if not version_line.lower().startswith("ffmpeg version"):
        raise FfmpegProbeError("FFmpeg version probe returned an unrecognized response")
    return FfmpegCapabilities(
        version_line=version_line,
        version_text=output["version"],
        build_configuration=output["build"],
        devices=output["devices"],
        encoders=output["encoders"],
        hardware_accelerators=output["hardware"],
    )


@dataclass(frozen=True)
class CameraInputProfile:
    width: int = TARGET_4K60.width
    height: int = TARGET_4K60.height
    fps: int = TARGET_4K60.fps
    pixel_format: str = "mjpeg"

    def verify_target(self, target: CaptureTargetProfile = TARGET_4K60) -> None:
        if (self.width, self.height, self.fps) != (target.width, target.height, target.fps):
            raise ValueError(
                f"camera input profile must be exactly {target.label}; "
                f"received {self.width}x{self.height}@{self.fps}"
            )
        if not self.pixel_format.strip():
            raise ValueError("camera input pixel format is required")


def build_device_list_command(tools: FfmpegTools, *, platform: str) -> list[str]:
    if platform == "win32":
        return [
            str(tools.ffmpeg),
            "-hide_banner",
            "-list_devices",
            "true",
            "-f",
            "dshow",
            "-i",
            "dummy",
        ]
    if platform.startswith("linux"):
        return [str(tools.ffmpeg), "-hide_banner", "-devices"]
    raise ValueError(f"unsupported camera platform {platform!r}")


def build_profile_list_command(
    tools: FfmpegTools,
    device_identifier: str,
    *,
    platform: str,
) -> list[str]:
    if platform == "win32":
        return [
            str(tools.ffmpeg),
            "-hide_banner",
            "-f",
            "dshow",
            "-list_options",
            "true",
            "-i",
            f"video={device_identifier}",
        ]
    if platform.startswith("linux"):
        return [
            str(tools.ffmpeg),
            "-hide_banner",
            "-f",
            "v4l2",
            "-list_formats",
            "all",
            "-i",
            device_identifier,
        ]
    raise ValueError(f"unsupported camera platform {platform!r}")


def build_camera_input_arguments(
    device_identifier: str,
    profile: CameraInputProfile,
    *,
    platform: str,
) -> list[str]:
    profile.verify_target()
    if platform == "win32":
        return [
            "-rtbufsize",
            "512M",
            "-thread_queue_size",
            "512",
            "-f",
            "dshow",
            "-video_size",
            f"{profile.width}x{profile.height}",
            "-framerate",
            str(profile.fps),
            "-vcodec",
            profile.pixel_format,
            "-i",
            f"video={device_identifier}",
        ]
    if platform.startswith("linux"):
        return [
            "-thread_queue_size",
            "512",
            "-f",
            "v4l2",
            "-input_format",
            profile.pixel_format,
            "-video_size",
            f"{profile.width}x{profile.height}",
            "-framerate",
            str(profile.fps),
            "-timestamps",
            "default",
            "-i",
            device_identifier,
        ]
    raise ValueError(f"unsupported camera platform {platform!r}")


@dataclass(frozen=True)
class StreamCopyEvidence:
    codec: str
    target_profile_verified: bool
    preview_decode_verified: bool

    @property
    def eligible(self) -> bool:
        return (
            self.codec.casefold() in {"h264", "avc1"}
            and self.target_profile_verified
            and self.preview_decode_verified
        )


@dataclass(frozen=True)
class EncoderSelection:
    name: str
    output_arguments: tuple[str, ...]
    stream_copy: bool = False
    probe_command: tuple[str, ...] = ()
    global_arguments: tuple[str, ...] = ()
    record_filter: str = ""


def _encoder_probe_command(tools: FfmpegTools, name: str) -> list[str]:
    command = [
        str(tools.ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if name == "h264_vaapi":
        command.extend(["-vaapi_device", "/dev/dri/renderD128"])
    command.extend(
        [
            "-f",
            "lavfi",
            "-i",
            "color=size=640x480:rate=30:duration=0.25",
            "-frames:v",
            "3",
        ]
    )
    if name == "h264_vaapi":
        command.extend(["-vf", "format=nv12,hwupload"])
    command.extend(["-c:v", name, "-f", "null", "-"])
    return command


def select_runtime_encoder(
    tools: FfmpegTools,
    *,
    runner: RunCommand = _run_text,
    stream_copy: StreamCopyEvidence | None = None,
    timeout: float = 15.0,
) -> EncoderSelection:
    if stream_copy is not None and stream_copy.eligible:
        return EncoderSelection(name="copy", output_arguments=("-c:v", "copy"), stream_copy=True)

    candidates = (
        ("h264_nvenc", ("-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "23", "-b:v", "0")),
        ("h264_qsv", ("-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "23")),
        ("h264_vaapi", ("-c:v", "h264_vaapi", "-qp", "23")),
        ("libx264", ("-c:v", "libx264", "-preset", "ultrafast", "-crf", "23")),
    )
    failures: list[str] = []
    for name, output_arguments in candidates:
        command = _encoder_probe_command(tools, name)
        result = runner(command, timeout=timeout)
        if result.returncode == 0:
            return EncoderSelection(
                name=name,
                output_arguments=output_arguments,
                probe_command=tuple(command),
                global_arguments=(
                    ("-vaapi_device", "/dev/dri/renderD128")
                    if name == "h264_vaapi"
                    else ()
                ),
                record_filter="format=nv12,hwupload" if name == "h264_vaapi" else "",
            )
        detail = (result.stderr or result.stdout or "unknown failure").strip().splitlines()
        failures.append(f"{name}: {detail[-1] if detail else 'unknown failure'}")
    raise FfmpegProbeError("No H.264 encoder completed a runtime encode probe: " + "; ".join(failures))


def build_capture_command(
    tools: FfmpegTools,
    *,
    input_arguments: Sequence[str],
    encoder: EncoderSelection,
    partial_path: Path,
    preview_width: int = 960,
    preview_height: int = 540,
    preview_fps: int = 10,
) -> list[str]:
    command = [
        str(tools.ffmpeg),
        "-hide_banner",
        "-loglevel",
        "info",
        "-stats_period",
        "0.25",
        "-nostats",
        "-y",
        *encoder.global_arguments,
        *input_arguments,
    ]
    preview_filter = (
        f"fps={preview_fps},scale={preview_width}:{preview_height}:"
        "flags=fast_bilinear"
    )
    if encoder.stream_copy:
        command.extend(
            [
                "-map",
                "0:v:0",
                *encoder.output_arguments,
                "-f",
                "matroska",
                str(partial_path),
                "-map",
                "0:v:0",
                "-vf",
                preview_filter,
            ]
        )
    else:
        record_chain = (
            f"[recordsrc]{encoder.record_filter}[record];"
            if encoder.record_filter
            else "[recordsrc]null[record];"
        )
        command.extend(
            [
                "-filter_complex",
                (
                    f"[0:v]split=2[recordsrc][preview];"
                    f"{record_chain}[preview]{preview_filter}[previewout]"
                ),
                "-map",
                "[record]",
                *encoder.output_arguments,
                "-pix_fmt",
                "yuv420p",
                "-fps_mode",
                "cfr",
                "-f",
                "matroska",
                str(partial_path),
                "-map",
                "[previewout]",
            ]
        )
    command.extend(
        [
            "-an",
            "-c:v",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
            "-progress",
            "pipe:2",
        ]
    )
    return command


@dataclass(frozen=True)
class FfmpegProgress:
    frame: int = 0
    fps: float = 0.0
    speed: float = 0.0
    output_time_us: int = 0
    total_size: int = 0
    duplicate_frames: int = 0
    dropped_frames: int = 0
    malformed_lines: int = 0
    state: str = ""


class ProgressParser:
    _INTEGER_FIELDS = {
        "frame": "frame",
        "out_time_us": "output_time_us",
        "total_size": "total_size",
        "dup_frames": "duplicate_frames",
        "drop_frames": "dropped_frames",
    }
    _FLOAT_FIELDS = {"fps": "fps"}

    def __init__(self) -> None:
        self.value = FfmpegProgress()

    def feed(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            return False
        key, raw_value = stripped.split("=", maxsplit=1)
        try:
            if key in self._INTEGER_FIELDS:
                self.value = replace(self.value, **{self._INTEGER_FIELDS[key]: int(raw_value)})
            elif key in self._FLOAT_FIELDS:
                self.value = replace(self.value, **{self._FLOAT_FIELDS[key]: float(raw_value)})
            elif key == "speed":
                speed = raw_value.removesuffix("x")
                self.value = replace(self.value, speed=float(speed) if speed not in {"", "N/A"} else 0.0)
            elif key == "progress":
                self.value = replace(self.value, state=raw_value)
            else:
                return False
        except ValueError:
            self.value = replace(self.value, malformed_lines=self.value.malformed_lines + 1)
        return True


_VIDEO_CODEC = re.compile(r"Video:\s*(?P<codec>[A-Za-z0-9_]+)")
_VIDEO_SIZE = re.compile(r"(?P<width>\d{2,5})x(?P<height>\d{2,5})")
_VIDEO_FPS = re.compile(r"(?P<fps>\d+(?:\.\d+)?)\s*fps\b")
_PIXEL_FORMAT = re.compile(
    r",\s*(?P<pixel>(?:yuv|yuva|nv|rgb|bgr|gray|gbr)[A-Za-z0-9_]+)(?:\([^)]*\))?\s*,"
)


def parse_negotiated_profile(line: str) -> NegotiatedCaptureProfile | None:
    if "Video:" not in line:
        return None
    codec = _VIDEO_CODEC.search(line)
    size = _VIDEO_SIZE.search(line)
    fps = _VIDEO_FPS.search(line)
    pixel = _PIXEL_FORMAT.search(line)
    if not all((codec, size, fps, pixel)):
        return None
    return NegotiatedCaptureProfile(
        width=int(size.group("width")),
        height=int(size.group("height")),
        fps=float(fps.group("fps")),
        pixel_format=pixel.group("pixel"),
        codec=codec.group("codec"),
    )


@dataclass(frozen=True)
class StorageEstimate:
    duration_seconds: float
    bytes_per_second: float
    recording_bytes: int
    required_free_bytes: int
    available_free_bytes: int

    @property
    def fits(self) -> bool:
        return self.available_free_bytes >= self.required_free_bytes


def estimate_storage(
    destination: Path,
    *,
    duration_seconds: float,
    bytes_per_second: float,
    reserve_bytes: int = 1_073_741_824,
    disk_usage: Callable[[Path], shutil._ntuple_diskusage] = shutil.disk_usage,
) -> StorageEstimate:
    if duration_seconds <= 0 or bytes_per_second <= 0 or reserve_bytes < 0:
        raise ValueError("duration, throughput, and reserve must be positive")
    recording_bytes = int(duration_seconds * bytes_per_second)
    available = disk_usage(destination).free
    return StorageEstimate(
        duration_seconds=duration_seconds,
        bytes_per_second=bytes_per_second,
        recording_bytes=recording_bytes,
        required_free_bytes=recording_bytes + reserve_bytes,
        available_free_bytes=available,
    )


@dataclass(frozen=True)
class VideoVerification:
    readable: bool
    codec: str = ""
    pixel_format: str = ""
    width: int = 0
    height: int = 0
    average_frame_rate: str = ""
    frames: int = 0
    duration_seconds: float = 0.0
    size_bytes: int = 0
    error: str = ""


def verify_video(
    tools: FfmpegTools,
    path: Path,
    *,
    runner: RunCommand = _run_text,
    timeout: float = 30.0,
) -> VideoVerification:
    command = [
        str(tools.ffprobe),
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,pix_fmt,width,height,avg_frame_rate,nb_read_frames",
        "-show_entries",
        "format=duration,size",
        "-of",
        "json",
        str(path),
    ]
    result = runner(command, timeout=timeout)
    if result.returncode != 0:
        return VideoVerification(readable=False, error=(result.stderr or result.stdout).strip())
    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        format_data = payload.get("format", {})
        frames = int(stream.get("nb_read_frames") or 0)
        duration = float(format_data.get("duration") or 0.0)
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        size = int(format_data.get("size") or path.stat().st_size)
    except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError, OSError) as exc:
        return VideoVerification(readable=False, error=f"invalid ffprobe response: {exc}")
    readable = width > 0 and height > 0 and size > 0 and (frames > 0 or duration > 0)
    return VideoVerification(
        readable=readable,
        codec=str(stream.get("codec_name") or ""),
        pixel_format=str(stream.get("pix_fmt") or ""),
        width=width,
        height=height,
        average_frame_rate=str(stream.get("avg_frame_rate") or ""),
        frames=frames,
        duration_seconds=duration,
        size_bytes=size,
        error="" if readable else "ffprobe found no readable video frames",
    )


__all__ = [
    "CameraInputProfile",
    "EncoderSelection",
    "FfmpegCapabilities",
    "FfmpegProbeError",
    "FfmpegProgress",
    "FfmpegTools",
    "FfmpegUnavailableError",
    "ProgressParser",
    "StorageEstimate",
    "StreamCopyEvidence",
    "VideoVerification",
    "build_camera_input_arguments",
    "build_capture_command",
    "build_device_list_command",
    "build_profile_list_command",
    "estimate_storage",
    "parse_negotiated_profile",
    "probe_capabilities",
    "select_runtime_encoder",
    "verify_video",
]
