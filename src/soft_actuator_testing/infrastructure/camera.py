"""Single-owner FFmpeg camera worker and hardware-free device/process fakes."""

from __future__ import annotations

import io
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from time import monotonic, sleep, time_ns
from typing import BinaryIO, Protocol

from soft_actuator_testing.application.camera_capture import (
    CameraDevice,
    CameraDeviceSource,
    CameraMode,
    CaptureEvidence,
    CaptureError,
    CaptureHealth,
    CapturePhase,
    CaptureResult,
    LatestFrameChannel,
    NegotiatedCaptureProfile,
    PreviewFrame,
    TARGET_4K60,
)

from .ffmpeg import (
    CameraInputProfile,
    EncoderSelection,
    FfmpegProbeError,
    FfmpegTools,
    ProgressParser,
    VideoVerification,
    build_camera_input_arguments,
    build_capture_command,
    build_device_list_command,
    build_profile_list_command,
    parse_camera_modes,
    parse_negotiated_profile,
    select_runtime_encoder,
    verify_video,
)


class ProcessLike(Protocol):
    stdin: BinaryIO | None
    stdout: BinaryIO | None
    stderr: BinaryIO | None
    returncode: int | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def send_signal(self, sig: int) -> None: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[Sequence[str]], ProcessLike]
Verification = Callable[[Path], VideoVerification]
HealthObserver = Callable[[CaptureHealth], None]
ThroughputObserver = Callable[[float, CaptureHealth], None]


class FfmpegCameraDeviceSource(CameraDeviceSource):
    """Enumerate DirectShow names or V4L2 nodes without opening a camera."""

    _DIRECTSHOW_NAME = re.compile(r'\]\s+"(?P<name>[^"]+)"\s+\(video\)')
    _V4L2_FORMAT_LISTING = re.compile(
        r"\b(?:Raw|Compressed)\s*:\s*.+\b\d{2,5}x\d{2,5}\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        tools: FfmpegTools,
        *,
        platform: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        linux_device_directory: Path = Path("/dev"),
    ) -> None:
        self._tools = tools
        self._platform = sys.platform if platform is None else platform
        self._runner = runner
        self._linux_device_directory = linux_device_directory

    def devices(self) -> Sequence[CameraDevice]:
        if self._platform == "win32":
            command = build_device_list_command(self._tools, platform=self._platform)
            result = self._runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            text = "\n".join((result.stdout or "", result.stderr or ""))
            names = tuple(dict.fromkeys(match.group("name") for match in self._DIRECTSHOW_NAME.finditer(text)))
            return tuple(
                self._device_with_modes(name, name, "dshow") for name in names
            )
        if self._platform.startswith("linux"):
            return tuple(
                self._device_with_modes(str(path), path.name, "v4l2")
                for path in sorted(self._linux_device_directory.glob("video*"))
                if path.exists()
            )
        raise ValueError(f"unsupported camera platform {self._platform!r}")

    def profile_command(self, device_identifier: str) -> tuple[str, ...]:
        return tuple(
            build_profile_list_command(
                self._tools,
                device_identifier,
                platform=self._platform,
            )
        )

    def _device_with_modes(
        self,
        identifier: str,
        name: str,
        backend: str,
    ) -> CameraDevice:
        command = self.profile_command(identifier)
        try:
            result = self._runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:
            return CameraDevice(
                identifier,
                name,
                backend,
                mode_probe_error=f"camera mode probe failed: {exc}",
            )
        text = "\n".join((result.stdout or "", result.stderr or ""))
        modes = parse_camera_modes(text)
        if backend == "v4l2" and not modes and self._V4L2_FORMAT_LISTING.search(text):
            # FFmpeg's V4L2 ``-list_formats all`` output names formats and
            # resolutions but does not include frame rates.  Do not invent a
            # 60 fps mode or block the device on incomplete evidence: startup
            # still requests and verifies the exact negotiated target profile.
            return CameraDevice(
                identifier,
                name,
                backend,
                mode_probe_warning=(
                    "FFmpeg V4L2 format listing omits frame rates; "
                    f"{TARGET_4K60.label} will be verified at capture startup."
                ),
            )
        if result.returncode != 0 and not modes:
            detail = text.strip().splitlines()
            return CameraDevice(
                identifier,
                name,
                backend,
                mode_probe_error=(
                    "camera mode probe failed"
                    + (f": {detail[-1]}" if detail else "")
                ),
            )
        if not modes:
            return CameraDevice(
                identifier,
                name,
                backend,
                mode_probe_error=(
                    "camera reported no parseable capture modes; "
                    f"{TARGET_4K60.label} support is unknown"
                ),
            )
        return CameraDevice(identifier, name, backend, modes=modes)


def _spawn_process(command: Sequence[str]) -> subprocess.Popen[bytes]:
    kwargs: dict[str, object] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(list(command), **kwargs)  # noqa: S603 - arguments are project-built


class FfmpegCaptureBackend:
    """Own one FFmpeg process and route every exit through one finalizer."""

    def __init__(
        self,
        tools: FfmpegTools,
        *,
        platform: str | None = None,
        input_profile: CameraInputProfile | None = None,
        encoder: EncoderSelection | None = None,
        encoder_selector: Callable[[FfmpegTools], EncoderSelection] = select_runtime_encoder,
        process_factory: ProcessFactory = _spawn_process,
        verifier: Verification | None = None,
        preview_width: int = 960,
        preview_height: int = 540,
        preview_fps: int = 10,
        graceful_timeout: float = 5.0,
        interrupt_timeout: float = 3.0,
        drainer_timeout: float = 1.0,
        poll_interval: float = 0.02,
        health_observer: HealthObserver | None = None,
        throughput_observer: ThroughputObserver | None = None,
    ) -> None:
        self._tools = tools
        self._platform = sys.platform if platform is None else platform
        self._input_profile = input_profile or CameraInputProfile()
        self._input_profile.verify_target()
        self._encoder = encoder
        self._encoder_selector = encoder_selector
        self._process_factory = process_factory
        self._verifier = verifier or (lambda path: verify_video(tools, path))
        self._preview_width = preview_width
        self._preview_height = preview_height
        self._preview_fps = preview_fps
        self._graceful_timeout = graceful_timeout
        self._interrupt_timeout = interrupt_timeout
        self._drainer_timeout = drainer_timeout
        self._poll_interval = poll_interval
        self._health_observer = health_observer
        self._throughput_observer = throughput_observer

        self._lock = RLock()
        self._stop_requested = Event()
        self._ready = Event()
        self._done = Event()
        self._process: ProcessLike | None = None
        self._owner: Thread | None = None
        self._drainers: list[Thread] = []
        self._progress = ProgressParser()
        self._frame_channel: LatestFrameChannel[PreviewFrame] = LatestFrameChannel()
        self._health = CaptureHealth(encoder=encoder.name if encoder else "")
        self._result: CaptureResult | None = None
        self._partial_path = Path("video.partial.mkv")
        self._final_path = Path("video.mkv")
        self._stop_reason = "operator"
        self._failure = ""
        self._negotiated: NegotiatedCaptureProfile | None = None
        self._file_progress = False
        self._last_file_size = 0
        self._saw_output_time = 0
        self._output_time_advanced = False
        self._started_monotonic = 0.0

    @property
    def frame_channel(self) -> LatestFrameChannel[PreviewFrame]:
        return self._frame_channel

    @property
    def health(self) -> CaptureHealth:
        with self._lock:
            return self._health

    @property
    def result(self) -> CaptureResult | None:
        with self._lock:
            return self._result

    @property
    def command(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(getattr(self, "_command", ()))

    def configure_input_mode(self, mode: CameraMode) -> None:
        """Use a mode explicitly reported by the selected camera."""

        profile = CameraInputProfile.from_mode(mode)
        with self._lock:
            if self._owner is not None and self._owner.is_alive():
                raise CaptureError("cannot change camera mode while capture is active")
            self._input_profile = profile

    def start(
        self,
        output_directory: Path,
        device_identifier: str,
        *,
        readiness_timeout: float,
    ) -> None:
        if readiness_timeout <= 0:
            raise ValueError("readiness_timeout must be positive")
        with self._lock:
            if self._owner is not None and self._owner.is_alive():
                raise CaptureError("camera capture already has an active owner")
            if self._encoder is None:
                try:
                    self._encoder = self._encoder_selector(self._tools)
                except FfmpegProbeError as exc:
                    raise CaptureError(
                        f"no supported runtime H.264 encoder is available: {exc}"
                    ) from exc
        output_directory = Path(output_directory)
        output_directory.mkdir(parents=True, exist_ok=True)
        partial_path = output_directory / "video.partial.mkv"
        final_path = output_directory / "video.mkv"
        if final_path.exists():
            raise CaptureError(f"refusing to overwrite completed recording {final_path}")
        if partial_path.exists():
            diagnostic = output_directory / f"video.partial.{time_ns()}.mkv"
            partial_path.replace(diagnostic)

        with self._lock:
            if self._owner is not None and self._owner.is_alive():
                raise CaptureError("camera capture already has an active owner")
            self._reset(partial_path, final_path)
            input_arguments = build_camera_input_arguments(
                device_identifier,
                self._input_profile,
                platform=self._platform,
            )
            self._command = build_capture_command(
                self._tools,
                input_arguments=input_arguments,
                encoder=self._encoder,
                partial_path=partial_path,
                preview_width=self._preview_width,
                preview_height=self._preview_height,
                preview_fps=self._preview_fps,
            )
            self._owner = Thread(
                target=self._run_owner,
                args=(readiness_timeout,),
                name="ffmpeg-camera-owner",
                daemon=True,
            )
            self._set_health(CapturePhase.STARTING)
            self._owner.start()

        deadline = monotonic() + readiness_timeout + self._graceful_timeout + 1.0
        while monotonic() < deadline:
            if self._ready.wait(self._poll_interval):
                return
            if self._done.is_set():
                break
        if not self._done.is_set():
            self.stop("startup-timeout")
        result = self.result
        detail = result.error if result is not None else "capture startup timed out"
        raise CaptureError(detail or "capture startup failed")

    def stop(self, reason: str = "operator", *, timeout: float | None = None) -> CaptureResult:
        with self._lock:
            if self._result is not None:
                return self._result
            owner = self._owner
            if owner is None:
                raise CaptureError("camera capture has not started")
            if not self._stop_requested.is_set():
                self._stop_reason = reason
                self._stop_requested.set()
                self._set_health(CapturePhase.STOPPING)
        wait_timeout = (
            self._graceful_timeout + self._interrupt_timeout + 2.0
            if timeout is None
            else timeout
        )
        if not self._done.wait(wait_timeout):
            raise CaptureError("camera owner did not finish cleanup before timeout")
        result = self.result
        if result is None:
            raise CaptureError("camera owner stopped without a result")
        return result

    def close(self, *, timeout: float | None = None) -> CaptureResult | None:
        with self._lock:
            if self._owner is None:
                return self._result
        return self.stop("close", timeout=timeout)

    def _reset(self, partial_path: Path, final_path: Path) -> None:
        self._stop_requested.clear()
        self._ready.clear()
        self._done.clear()
        self._process = None
        self._drainers = []
        self._progress = ProgressParser()
        self._frame_channel = LatestFrameChannel()
        assert self._encoder is not None
        self._health = CaptureHealth(phase=CapturePhase.STARTING, encoder=self._encoder.name)
        self._result = None
        self._partial_path = partial_path
        self._final_path = final_path
        self._stop_reason = "operator"
        self._failure = ""
        self._negotiated = None
        self._file_progress = False
        self._last_file_size = 0
        self._saw_output_time = 0
        self._output_time_advanced = False
        self._started_monotonic = monotonic()

    def _run_owner(self, readiness_timeout: float) -> None:
        try:
            self._process = self._process_factory(self._command)
            if self._process.stdout is None or self._process.stderr is None:
                raise CaptureError("FFmpeg process did not expose preview/progress pipes")
            self._drainers = [
                Thread(target=self._drain_preview, name="ffmpeg-preview-drainer", daemon=True),
                Thread(target=self._drain_progress, name="ffmpeg-progress-drainer", daemon=True),
            ]
            for drainer in self._drainers:
                drainer.start()

            startup_deadline = monotonic() + readiness_timeout
            while not self._stop_requested.is_set() and monotonic() < startup_deadline:
                self._sample()
                if self._failure:
                    self._stop_reason = "startup-failure"
                    self._stop_requested.set()
                    break
                if self._process.poll() is not None:
                    self._failure = f"FFmpeg exited during startup with code {self._process.returncode}"
                    self._stop_reason = "startup-failure"
                    self._stop_requested.set()
                    break
                if any(not drainer.is_alive() for drainer in self._drainers):
                    self._failure = "FFmpeg preview/progress drainer stopped during startup"
                    self._stop_reason = "startup-failure"
                    self._stop_requested.set()
                    break
                if self._startup_ready():
                    self._set_health(
                        CapturePhase.RECORDING,
                        ready=True,
                        evidence=CaptureEvidence(startup_proven=True),
                    )
                    self._ready.set()
                    break
                sleep(self._poll_interval)
            if not self._ready.is_set() and not self._stop_requested.is_set():
                self._failure = (
                    "camera startup timed out before negotiated 3840x2160@60, "
                    "frame, file-progress, and preview proof"
                )
                self._stop_reason = "startup-timeout"
                self._stop_requested.set()

            while self._ready.is_set() and not self._stop_requested.is_set():
                self._sample()
                if self._process.poll() is not None:
                    self._failure = f"FFmpeg disconnected/exited with code {self._process.returncode}"
                    self._stop_reason = "disconnect"
                    self._stop_requested.set()
                    break
                if any(not drainer.is_alive() for drainer in self._drainers):
                    self._failure = "FFmpeg preview/progress drainer stopped while recording"
                    self._stop_reason = "fault"
                    self._stop_requested.set()
                    break
                sleep(self._poll_interval)
        except Exception as exc:
            self._failure = str(exc)
            self._stop_reason = "fault"
            self._stop_requested.set()
        finally:
            self._finalize()

    def _startup_ready(self) -> bool:
        with self._lock:
            profile = self._negotiated
            progress = self._progress.value
        if profile is None:
            return False
        try:
            profile.verify(
                TARGET_4K60,
                expected_pixel_format=self._input_profile.pixel_format,
            )
        except ValueError as exc:
            self._failure = str(exc)
            return False
        return (
            progress.frame >= 1
            and self._output_time_advanced
            and self._file_progress
            and self._frame_channel.stats.produced >= 1
        )

    def _drain_preview(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        frame_size = self._preview_width * self._preview_height * 3
        frame_index = 0
        while True:
            payload = self._read_exact(process.stdout, frame_size)
            if len(payload) != frame_size:
                if payload:
                    self._add_warning(f"truncated preview frame ({len(payload)}/{frame_size} bytes)")
                return
            self._frame_channel.publish(
                PreviewFrame(
                    index=frame_index,
                    width=self._preview_width,
                    height=self._preview_height,
                    rgb_bytes=payload,
                    captured_monotonic=monotonic(),
                )
            )
            frame_index += 1

    @staticmethod
    def _read_exact(stream: BinaryIO, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = stream.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _drain_progress(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        warning_terms = (
            "overrun",
            "corrupt",
            "discontinu",
            "disconnect",
            "invalid",
            "error",
        )
        while True:
            payload = process.stderr.readline()
            if not payload:
                return
            line = payload.decode("utf-8", errors="replace").strip()
            with self._lock:
                before = self._progress.value.output_time_us
                recognized = self._progress.feed(line)
                after = self._progress.value.output_time_us
                if after > 0:
                    if self._saw_output_time > 0 and after > self._saw_output_time:
                        self._output_time_advanced = True
                    self._saw_output_time = max(self._saw_output_time, after)
                if not recognized:
                    profile = parse_negotiated_profile(line)
                    if profile is not None and self._negotiated is None:
                        self._negotiated = profile
            if not recognized:
                if any(term in line.casefold() for term in warning_terms):
                    self._add_warning(line)
            elif after > before:
                self._sample()

    def _sample(self) -> None:
        try:
            size = self._partial_path.stat().st_size
        except OSError:
            size = 0
        with self._lock:
            if size > self._last_file_size:
                self._file_progress = True
            self._last_file_size = max(self._last_file_size, size)
            progress = self._progress.value
            negotiated = self._negotiated
            current = self._health
            output_bytes = max(size, progress.total_size)
            health = CaptureHealth(
                phase=current.phase,
                frame=progress.frame,
                fps=progress.fps,
                speed=progress.speed,
                output_time_us=progress.output_time_us,
                output_bytes=output_bytes,
                duplicate_frames=progress.duplicate_frames,
                dropped_frames=progress.dropped_frames,
                malformed_progress_lines=progress.malformed_lines,
                negotiated_profile=negotiated,
                encoder=self._encoder.name,
                preview=self._frame_channel.stats,
                warnings=current.warnings,
                ready=self._ready.is_set(),
                clean=current.clean,
                evidence=current.evidence,
            )
            self._health = health
        self._notify_health(health)
        if self._throughput_observer and progress.output_time_us > 0:
            throughput = output_bytes / (progress.output_time_us / 1_000_000)
            try:
                self._throughput_observer(throughput, health)
            except Exception as exc:
                self._add_warning(f"capture throughput observer failed: {exc}")

    def _set_health(
        self,
        phase: CapturePhase,
        *,
        ready: bool | None = None,
        clean: bool | None = None,
        evidence: CaptureEvidence | None = None,
    ) -> None:
        with self._lock:
            values: dict[str, object] = {"phase": phase}
            if ready is not None:
                values["ready"] = ready
            if clean is not None:
                values["clean"] = clean
            if evidence is not None:
                values["evidence"] = evidence
            self._health = self._health.__class__(**{**self._health.__dict__, **values})
            health = self._health
        self._notify_health(health)

    def _add_warning(self, warning: str) -> None:
        with self._lock:
            if warning in self._health.warnings:
                return
            self._health = self._health.__class__(
                **{**self._health.__dict__, "warnings": (*self._health.warnings, warning)}
            )

    def _notify_health(self, health: CaptureHealth) -> None:
        if self._health_observer is not None:
            try:
                self._health_observer(health)
            except Exception as exc:
                self._add_warning(f"capture health observer failed: {exc}")

    def _finalize(self) -> None:
        clean = not self._failure
        process = self._process
        cooperative_shutdown = False
        shutdown_escalated = False
        process_exit_code: int | None = (
            process.returncode if process is not None else None
        )
        if process is None:
            clean = False
            self._failure = self._failure or "FFmpeg process was never created"
        elif process.poll() is None:
            quit_sent = self._write_quit(process)
            if not quit_sent:
                clean = False
                self._failure = self._failure or "could not request cooperative FFmpeg shutdown"
            try:
                if quit_sent:
                    process_exit_code = process.wait(timeout=self._graceful_timeout)
                    if process_exit_code == 0:
                        cooperative_shutdown = True
                    else:
                        clean = False
                        self._failure = self._failure or (
                            "FFmpeg exited with code "
                            f"{process_exit_code} after cooperative shutdown request"
                        )
            except subprocess.TimeoutExpired:
                clean = False
                shutdown_escalated = True
                self._failure = self._failure or (
                    "FFmpeg did not exit after cooperative shutdown request"
                )
            except Exception as exc:
                clean = False
                self._failure = self._failure or f"FFmpeg graceful cleanup failed: {exc}"

            if process.poll() is None:
                shutdown_escalated = True
                try:
                    self._interrupt(process)
                    process_exit_code = process.wait(timeout=self._interrupt_timeout)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                        process_exit_code = process.wait(timeout=self._interrupt_timeout)
                    except Exception as exc:
                        detail = f"FFmpeg kill cleanup failed: {exc}"
                        self._failure = (
                            f"{self._failure}; {detail}" if self._failure else detail
                        )
                except Exception as exc:
                    detail = f"FFmpeg interrupt cleanup failed: {exc}"
                    self._failure = (
                        f"{self._failure}; {detail}" if self._failure else detail
                    )
                clean = False
        else:
            # A process that was already gone was not confirmed to have received
            # our cooperative quit request; preserve its partial for diagnosis.
            clean = False
            self._failure = self._failure or (
                f"FFmpeg exited before cooperative shutdown with code {process.returncode}"
            )

        drainer_deadline = monotonic() + self._drainer_timeout
        drainers_stopped = True
        for drainer in self._drainers:
            drainer.join(timeout=max(0.0, drainer_deadline - monotonic()))
            if drainer.is_alive():
                clean = False
                drainers_stopped = False
                self._add_warning(f"{drainer.name} did not drain before cleanup timeout")
        self._sample()
        verification = self._verify_partial()
        video_path: Path | None = None
        startup_proven = self._ready.is_set()
        promotable = (
            verification.readable
            and startup_proven
            and cooperative_shutdown
            and drainers_stopped
            and not self._failure
            and clean
        )
        if promotable:
            try:
                self._partial_path.replace(self._final_path)
                video_path = self._final_path
            except OSError as exc:
                self._failure = self._failure or f"could not promote verified recording: {exc}"
                clean = False
        elif self._partial_path.exists():
            self._failure = self._failure or verification.error or (
                "recording did not meet clean cooperative promotion requirements"
            )
            clean = False

        clean = (
            clean
            and verification.readable
            and startup_proven
            and cooperative_shutdown
            and drainers_stopped
            and video_path is not None
        )
        evidence = CaptureEvidence(
            startup_proven=startup_proven,
            cooperative_shutdown=cooperative_shutdown,
            process_exit_code=process_exit_code,
            drainers_stopped=drainers_stopped,
            verification_readable=verification.readable,
            promoted=video_path is not None,
            shutdown_escalated=shutdown_escalated,
        )
        phase = CapturePhase.COMPLETED if clean else CapturePhase.FAULT
        self._set_health(phase, ready=False, clean=clean, evidence=evidence)
        with self._lock:
            self._result = CaptureResult(
                completion_reason=self._stop_reason,
                video_path=video_path,
                partial_path=self._partial_path,
                readable=verification.readable,
                clean=clean,
                health=self._health,
                error=self._failure or verification.error,
                evidence=evidence,
            )
        self._done.set()

    def _verify_partial(self) -> VideoVerification:
        if not self._partial_path.exists():
            return VideoVerification(readable=False, error="FFmpeg produced no partial recording")
        try:
            return self._verifier(self._partial_path)
        except Exception as exc:
            return VideoVerification(readable=False, error=f"ffprobe verification failed: {exc}")

    @staticmethod
    def _write_quit(process: ProcessLike) -> bool:
        if process.stdin is None:
            return False
        try:
            process.stdin.write(b"q\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    @staticmethod
    def _interrupt(process: ProcessLike) -> None:
        sig = (
            signal.CTRL_BREAK_EVENT
            if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT")
            else signal.SIGINT
        )
        process.send_signal(sig)


class FakeCameraDeviceSource(CameraDeviceSource):
    """Deterministic device source for the default test suite and demos."""

    def __init__(self, devices: Sequence[CameraDevice] = ()) -> None:
        self._devices = tuple(devices)
        self.error: Exception | None = None

    def devices(self) -> Sequence[CameraDevice]:
        if self.error is not None:
            raise self.error
        return self._devices


class _ControlSink(io.BytesIO):
    def __init__(self, process: ScriptedProcess) -> None:
        super().__init__()
        self._process = process

    def flush(self) -> None:
        if self.getvalue().endswith(b"q\n"):
            self._process.finish(0)


class _ScriptedStream:
    def __init__(self, payload: bytes, finished: Event) -> None:
        self._source = io.BytesIO(payload)
        self._finished = finished

    def read(self, size: int = -1) -> bytes:
        value = self._source.read(size)
        if value:
            return value
        self._finished.wait()
        return b""

    def readline(self, size: int = -1) -> bytes:
        value = self._source.readline(size)
        if value:
            return value
        self._finished.wait()
        return b""


class ScriptedProcess:
    """In-memory process with controllable timeout/escalation behavior."""

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        quit_exits: bool = True,
        interrupt_exits: bool = True,
    ) -> None:
        self.returncode: int | None = None
        self.quit_exits = quit_exits
        self.interrupt_exits = interrupt_exits
        self.signals: list[int] = []
        self.killed = False
        self._finished = Event()
        self.stdout: BinaryIO = _ScriptedStream(stdout, self._finished)  # type: ignore[assignment]
        self.stderr: BinaryIO = _ScriptedStream(stderr, self._finished)  # type: ignore[assignment]
        self.stdin: BinaryIO = _ControlSink(self)

    def finish(self, returncode: int) -> None:
        if self.returncode is None and (
            self.quit_exits or returncode != 0
        ):
            self.returncode = returncode
            self._finished.set()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self._finished.wait(timeout):
            raise subprocess.TimeoutExpired("scripted-ffmpeg", timeout)
        assert self.returncode is not None
        return self.returncode

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if self.interrupt_exits:
            self.returncode = 255
            self._finished.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._finished.set()


class FakeProcessFactory:
    """Records commands and creates scripted processes without FFmpeg."""

    def __init__(
        self,
        supplier: Callable[[Sequence[str]], ScriptedProcess],
    ) -> None:
        self._supplier = supplier
        self.commands: list[tuple[str, ...]] = []
        self.processes: list[ScriptedProcess] = []

    def __call__(self, command: Sequence[str]) -> ScriptedProcess:
        self.commands.append(tuple(command))
        process = self._supplier(command)
        self.processes.append(process)
        return process


__all__ = [
    "FakeCameraDeviceSource",
    "FakeProcessFactory",
    "FfmpegCameraDeviceSource",
    "FfmpegCaptureBackend",
    "ProcessFactory",
    "ProcessLike",
    "ScriptedProcess",
]
