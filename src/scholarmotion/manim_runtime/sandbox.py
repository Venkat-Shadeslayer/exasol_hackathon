from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .validator import validate_generated_code


def _tail_error(output: str, *, limit: int = 3000) -> str:
    output = output.strip()
    if not output:
        return "Manim failed or produced an empty render (no output captured)."
    marker = "LaTeX compilation error"
    marker_index = output.find(marker)
    if marker_index != -1:
        excerpt = output[marker_index : marker_index + 1200]
        return f"{excerpt}\n\n...\n{output[-limit:]}"
    return output[-limit:]


@dataclass
class RenderResult:
    success: bool
    video_path: str | None
    log_path: str
    metadata_path: str
    keyframe_paths: list[str]
    duration_seconds: float
    return_code: int
    error: str | None = None
    bounds_path: str | None = None


_BOUNDS_PATH_TOKEN = "__SCHOLARMOTION_BOUNDS_PATH__"

_BOUNDS_INSTRUMENTATION = """

import json as _sm_json
from manim import Scene as _SMScene

_sm_snapshots = []


def _sm_capture_snapshot(scene):
    bounds = []
    for index, mob in enumerate(scene.mobjects):
        try:
            left = mob.get_left()[0]
            right = mob.get_right()[0]
            bottom = mob.get_bottom()[1]
            top = mob.get_top()[1]
        except Exception:
            continue
        if (right - left) < 1e-6 and (top - bottom) < 1e-6:
            continue
        is_text = type(mob).__name__ in ("Text", "MathTex", "Tex", "MarkupText", "SafeTitle")
        bounds.append(
            {
                "name": f"{type(mob).__name__}_{index}",
                "left": float(left),
                "right": float(right),
                "bottom": float(bottom),
                "top": float(top),
                "text_height": float(top - bottom) if is_text else None,
                "reserved_region": getattr(mob, "reserved_region", None),
            }
        )
    _sm_snapshots.append(bounds)


_sm_original_play = _SMScene.play
_sm_original_wait = _SMScene.wait
_sm_original_render = _SMScene.render


def _sm_patched_play(self, *args, **kwargs):
    result = _sm_original_play(self, *args, **kwargs)
    try:
        _sm_capture_snapshot(self)
    except Exception:
        pass
    return result


def _sm_patched_wait(self, *args, **kwargs):
    result = _sm_original_wait(self, *args, **kwargs)
    try:
        _sm_capture_snapshot(self)
    except Exception:
        pass
    return result


def _sm_patched_render(self, *args, **kwargs):
    result = _sm_original_render(self, *args, **kwargs)
    try:
        with open("__SCHOLARMOTION_BOUNDS_PATH__", "w") as _f:
            _sm_json.dump(_sm_snapshots, _f)
    except Exception:
        pass
    return result


_SMScene.play = _sm_patched_play
_SMScene.wait = _sm_patched_wait
_SMScene.render = _sm_patched_render
"""


class SandboxRenderer:
    """Static gate plus constrained process runner.

    Container-level network isolation is configured in docker-compose. The process runner
    additionally passes an allowlisted environment and a private working directory.
    """

    def __init__(
        self, manim_binary: str = "manim", timeout_seconds: int = 180, mock_missing: bool = True
    ):
        self.manim_binary = manim_binary
        self.timeout_seconds = timeout_seconds
        self.mock_missing = mock_missing

    def render(
        self,
        code: str,
        scene_class: str,
        output_dir: str | Path,
        *,
        assets_dir: str | Path | None = None,
    ) -> RenderResult:
        started = time.monotonic()
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        log_path = output / "render.log"
        metadata_path = output / "metadata.json"
        validation = validate_generated_code(code)
        if not validation.accepted or scene_class not in validation.scene_classes:
            error = "; ".join(validation.errors or [f"missing scene class {scene_class}"])
            log_path.write_text(error, encoding="utf-8")
            result = RenderResult(
                False,
                None,
                str(log_path),
                str(metadata_path),
                [],
                time.monotonic() - started,
                2,
                error,
            )
            metadata_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
            return result
        executable = shutil.which(self.manim_binary)
        if not executable and self.mock_missing:
            video_path = output / "draft.mp4"
            video_path.write_bytes(b"SCHOLARMOTION_DETERMINISTIC_RENDER\n" + scene_class.encode())
            log_path.write_text(
                "Manim unavailable; deterministic render artifact created.\n", encoding="utf-8"
            )
            result = RenderResult(
                True,
                str(video_path),
                str(log_path),
                str(metadata_path),
                [],
                time.monotonic() - started,
                0,
            )
            metadata_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
            return result
        bounds_path = output / "bounds.json"
        with tempfile.TemporaryDirectory(prefix="scholarmotion-render-") as temp:
            work = Path(temp)
            code_path = work / "scene.py"
            instrumented = code + _BOUNDS_INSTRUMENTATION.replace(
                _BOUNDS_PATH_TOKEN, str(bounds_path)
            )
            code_path.write_text(instrumented, encoding="utf-8")
            if assets_dir:
                source_assets = Path(assets_dir).resolve()
                if source_assets.exists():
                    shutil.copytree(source_assets, work / "assets", dirs_exist_ok=True)
            env = {
                "PATH": os.pathsep.join(
                    filter(
                        None,
                        [
                            str(Path(executable).parent) if executable else "",
                            os.environ.get("PATH", ""),
                        ],
                    )
                ),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                # User-level Conda runtimes may supply Cairo/Pango libraries
                # required by a project-local Manim install.
                "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH", ""),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_PROXY": "*",
            }
            command = [
                executable or self.manim_binary,
                "-ql",
                "--disable_caching",
                "--media_dir",
                str(output),
                str(code_path),
                scene_class,
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    shell=False,
                    check=False,
                )
                log_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
                candidates = sorted(
                    output.rglob(f"{scene_class}.mp4"), key=lambda p: p.stat().st_mtime
                )
                video_path = str(candidates[-1]) if candidates else None
                success = (
                    completed.returncode == 0
                    and bool(video_path)
                    and Path(video_path).stat().st_size > 0
                )
                error = None if success else _tail_error(completed.stdout + "\n" + completed.stderr)
                result = RenderResult(
                    success,
                    video_path,
                    str(log_path),
                    str(metadata_path),
                    [],
                    time.monotonic() - started,
                    completed.returncode,
                    error,
                    str(bounds_path) if success and bounds_path.exists() else None,
                )
            except subprocess.TimeoutExpired as exc:
                def _decode(value: str | bytes | None) -> str:
                    if value is None:
                        return ""
                    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value

                log_path.write_text(_decode(exc.stdout) + "\n" + _decode(exc.stderr), encoding="utf-8")
                result = RenderResult(
                    False,
                    None,
                    str(log_path),
                    str(metadata_path),
                    [],
                    time.monotonic() - started,
                    124,
                    "render timeout",
                )
        metadata_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
        return result
