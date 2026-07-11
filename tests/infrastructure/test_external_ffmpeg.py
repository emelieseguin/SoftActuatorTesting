from __future__ import annotations

import subprocess

import pytest

from soft_actuator_testing.infrastructure.ffmpeg import (
    FfmpegTools,
    probe_capabilities,
    select_runtime_encoder,
)


pytestmark = pytest.mark.external_ffmpeg


def test_installed_ffmpeg_can_probe_and_encode_a_synthetic_source() -> None:
    tools = FfmpegTools.discover()
    capabilities = probe_capabilities(tools)
    assert capabilities.version_line.startswith("ffmpeg version")
    selected = select_runtime_encoder(tools)
    result = subprocess.run(
        list(selected.probe_command),
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
