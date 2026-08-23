"""Regression cover for scene audio/video muxing.

A scene's animation and its narration are produced independently and rarely
match in length. Muxing them with ``-shortest`` truncated the longer stream,
which discarded most of the voiceover whenever the animation was shorter — a
7-second video against 490 seconds of narration in the case that prompted this.
"""

from __future__ import annotations

from pathlib import Path

from scholarmotion.media.ffmpeg import FFmpegAssembler


class _FixedDurations(FFmpegAssembler):
    """Assembler with ffprobe stubbed out, so the command logic is testable."""

    def __init__(self, durations: dict[str, float | None]):
        super().__init__()
        self._durations = durations

    def _probe_duration(self, path: str) -> float | None:
        return self._durations.get(path)


def _command(video_seconds, audio_seconds) -> list[str]:
    assembler = _FixedDurations({"v.mp4": video_seconds, "a.wav": audio_seconds})
    return assembler._mux_command("ffmpeg", "v.mp4", "a.wav", Path("out.mp4"))


def test_narration_longer_than_animation_is_not_truncated():
    command = _command(1.0, 61.2)
    joined = " ".join(command)
    assert "-shortest" not in joined
    assert "tpad=stop_mode=clone:stop_duration=60.200" in joined
    assert "-t 61.200" in joined


def test_animation_longer_than_narration_pads_audio_with_silence():
    command = _command(45.0, 30.0)
    joined = " ".join(command)
    assert "-shortest" not in joined
    assert "apad=pad_dur=15.000" in joined
    assert "-t 45.000" in joined


def test_matching_durations_keep_the_fast_stream_copy():
    command = _command(30.0, 30.02)
    joined = " ".join(command)
    assert "-c:v copy" in joined
    assert "tpad" not in joined


def test_unprobeable_input_falls_back_to_stream_copy():
    """Without ffprobe there is nothing to align against; do not re-encode."""
    command = _command(None, 61.2)
    joined = " ".join(command)
    assert "-c:v copy" in joined
    assert "tpad" not in joined


def test_padded_output_is_encoded_for_broad_playback():
    command = _command(1.0, 61.2)
    joined = " ".join(command)
    assert "-c:v libx264" in joined
    assert "-pix_fmt yuv420p" in joined
    assert "-c:a aac" in joined


def test_output_audio_is_pinned_to_a_playable_rate():
    """loudnorm emits 96 kHz AAC unless the output rate is pinned.

    QuickTime and most players cannot decode 96 kHz AAC and play silence, so a
    video that measures as having audio still sounds empty to the user.
    """
    import shutil
    from unittest.mock import patch

    from scholarmotion.media.ffmpeg import FFmpegAssembler

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        raise SystemExit  # stop before ffmpeg actually runs

    assembler = FFmpegAssembler()
    with patch.object(shutil, "which", return_value="/usr/bin/ffmpeg"):
        with patch("subprocess.run", side_effect=fake_run):
            try:
                assembler.assemble([__file__], "out.mp4", audio_normalization=True)
            except (SystemExit, FileNotFoundError, ValueError):
                pass

    command = captured.get("command", [])
    assert "-ar" in command and command[command.index("-ar") + 1] == "48000"
    assert "-ac" in command and command[command.index("-ac") + 1] == "2"
