from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from scholarmotion.agents.code_generator import generate_scene_code
from scholarmotion.agents.curriculum_agent import build_curriculum
from scholarmotion.agents.knowledge_gatherer import gather_knowledge
from scholarmotion.agents.learner_profiler import profile_request
from scholarmotion.agents.manager import Manager, RepairAction
from scholarmotion.agents.pedagogical_agent import build_teaching_dossier
from scholarmotion.agents.scene_compiler import compile_scene_specs
from scholarmotion.agents.script_writer import write_script
from scholarmotion.agents.storyboard_agent import create_storyboard
from scholarmotion.manim_runtime.instrumentation import ObjectBounds
from scholarmotion.manim_runtime.sandbox import RenderResult, SandboxRenderer
from scholarmotion.manim_runtime.timing import reconcile_timing
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.media.subtitles import cues_to_srt, cues_to_vtt, merge_scene_cues
from scholarmotion.media.timeline import build_timeline
from scholarmotion.memory.correction_memory import CorrectionMemory
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.schemas import SceneSpec, Timeline, TTSResult, VerificationIssue, VerificationReport
from scholarmotion.verification.aggregator import aggregate_reports
from scholarmotion.verification.layout import verify_layout
from scholarmotion.verification.pedagogy import verify_pedagogy
from scholarmotion.verification.render import verify_no_raw_latex_text, verify_render


def _verify_render_layout(scene_id: str, result: RenderResult) -> list[VerificationIssue]:
    if not result.success or not result.bounds_path:
        return []
    try:
        snapshots = json.loads(Path(result.bounds_path).read_text())
    except Exception:
        return []
    seen: set[tuple] = set()
    issues: list[VerificationIssue] = []
    for keyframe_index, snapshot in enumerate(snapshots):
        objects = [ObjectBounds(**item) for item in snapshot]
        for issue in verify_layout(scene_id, objects):
            key = (issue.category, tuple(issue.objects))
            if key in seen:
                continue
            seen.add(key)
            issue.frames = [keyframe_index]
            prefix = (
                f"At keyframe {keyframe_index} (the state right after the "
                f"{keyframe_index + 1}th self.play()/self.wait() call in construct(), not "
                "necessarily the final frame): "
            )
            issue.description = prefix + issue.description
            issue.suggested_repair = prefix + issue.suggested_repair
            issues.append(issue)
    return issues


@dataclass
class ProducedScene:
    spec: SceneSpec
    code_path: str
    audio: TTSResult
    render_path: str
    report: VerificationReport
    render_version: int = 1
    code_version: int = 1
    audio_version: int = 1


@dataclass
class BuildResult:
    project_id: str
    scenes: list[ProducedScene]
    video_path: str
    timeline: Timeline
    srt_path: str
    vtt_path: str
    events: list[str]
    blocked_scenes: list[dict] = field(default_factory=list)


# Layout faults are placement problems: something overlaps, sits too low, or is
# small. They make a scene look worse, not wrong. Everything else — a render
# crash, bad mathematics, an ungrounded claim, a pedagogy violation — changes
# what the scene actually teaches and must never be shipped by falling back.
COSMETIC_ISSUE_PREFIXES = ("layout.",)


def _only_cosmetic(report: VerificationReport | None) -> bool:
    """True when every outstanding issue is presentation-only.

    Used to decide whether an imperfect but rendered scene is better than no
    scene at all. Deliberately conservative: an empty/missing report is not
    treated as cosmetic, and a single critical issue disqualifies the scene.
    """
    if report is None or not report.issues:
        return False
    return all(
        issue.category.startswith(COSMETIC_ISSUE_PREFIXES) and issue.severity != "critical"
        for issue in report.issues
    )


class ScholarMotionPipeline:
    """Provider-neutral workflow; Celery tasks invoke the same stage methods."""

    def __init__(
        self,
        *,
        llm,
        tts,
        embeddings,
        storage: LocalObjectStore,
        renderer: SandboxRenderer | None = None,
        assembler: FFmpegAssembler | None = None,
        correction_memory: CorrectionMemory | None = None,
        max_concurrent_scenes: int = 4,
    ):
        self.llm = llm
        self.tts = tts
        self.embeddings = embeddings
        self.storage = storage
        self.renderer = renderer or SandboxRenderer()
        self.assembler = assembler or FFmpegAssembler()
        self.corrections = correction_memory or CorrectionMemory(
            "knowledge/corrections/correction_file.md"
        )
        self.manager = Manager()
        self.max_concurrent_scenes = max(1, max_concurrent_scenes)

    def _json(self, key: str, value) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return self.storage.put_bytes(key, json.dumps(value, indent=2, default=str).encode())

    async def build(
        self,
        project_id: str,
        request: str,
        *,
        duration_minutes: float = 5,
        language: str = "English",
        retrieved_chunks: list[dict] | None = None,
    ) -> BuildResult:
        events = ["project.started"]
        profile = profile_request(
            request,
            target_duration_minutes=duration_minutes,
            language=language,
            has_paper=any(
                chunk.get("document_kind") == "paper" for chunk in (retrieved_chunks or [])
            ),
        )
        self._json(f"{project_id}/artifacts/profile/v1.json", profile)
        cards = gather_knowledge(
            retrieved_chunks
            or [
                {
                    "id": "demo-grounding",
                    "text": f"A grounded introductory explanation of {profile.topic}.",
                    "content_type": "definition",
                }
            ]
        )
        self._json(
            f"{project_id}/artifacts/retrieval/v1.json", [card.model_dump() for card in cards]
        )
        events.append("retrieval.complete")
        curriculum = build_curriculum(profile, cards)
        self._json(f"{project_id}/artifacts/curriculum/v1.json", curriculum)
        events.append("curriculum.complete")
        dossier = build_teaching_dossier(profile, curriculum, cards)
        self._json(f"{project_id}/artifacts/pedagogy/v1.json", dossier)
        blocks = await write_script(self.llm, curriculum, dossier)
        self._json(
            f"{project_id}/artifacts/script/v1.json", [block.model_dump() for block in blocks]
        )
        events.append("script.complete")
        storyboard = create_storyboard(blocks)
        self._json(
            f"{project_id}/artifacts/storyboard/v1.json",
            {key: [beat.model_dump() for beat in beats] for key, beats in storyboard.items()},
        )
        events.append("storyboard.complete")
        specs = compile_scene_specs(blocks, storyboard)
        events.append("scenes.created")
        semaphore = asyncio.Semaphore(self.max_concurrent_scenes)

        async def limited(spec: SceneSpec) -> ProducedScene:
            async with semaphore:
                return await self.produce_scene(project_id, spec, events)

        results = await asyncio.gather(
            *(limited(spec) for spec in specs), return_exceptions=True
        )
        scenes = [result for result in results if isinstance(result, ProducedScene)]
        blocked_scenes = [
            {
                "scene_id": spec.scene_id,
                "title": spec.title,
                "error": f"{type(result).__name__}: {result}",
            }
            for spec, result in zip(specs, results)
            if not isinstance(result, ProducedScene)
        ]
        for blocked in blocked_scenes:
            events.append(f"scene.blocked:{blocked['scene_id']}")
        if not scenes:
            raise RuntimeError(f"every scene was blocked: {blocked_scenes}")
        events.append("video.assembling")
        video_key = f"{project_id}/video/v1/final.mp4"
        video_path = self.storage.local_path(video_key)
        assembly = self.assembler.assemble(
            [scene.render_path for scene in scenes],
            video_path,
            audio_paths=[scene.audio.audio_path for scene in scenes],
        )
        if not assembly.success:
            raise RuntimeError(f"video assembly failed: {assembly.log[-1000:]}")
        timeline = build_timeline(
            [
                (
                    scene.spec.scene_id,
                    scene.audio.duration_seconds,
                    scene.render_version,
                    scene.render_path,
                )
                for scene in scenes
            ]
        )
        self._json(f"{project_id}/video/v1/timeline.json", timeline)
        cues = merge_scene_cues(
            [
                (segment.start, scene.audio.sentence_timings)
                for segment, scene in zip(timeline.scenes, scenes)
            ]
        )
        srt_key, vtt_key = (
            f"{project_id}/video/v1/subtitles.srt",
            f"{project_id}/video/v1/subtitles.vtt",
        )
        self.storage.put_bytes(srt_key, cues_to_srt(cues).encode())
        self.storage.put_bytes(vtt_key, cues_to_vtt(cues).encode())
        events.append("video.complete")
        return BuildResult(
            project_id,
            scenes,
            str(video_path),
            timeline,
            str(self.storage.local_path(srt_key)),
            str(self.storage.local_path(vtt_key)),
            events,
            blocked_scenes,
        )

    async def produce_scene(
        self,
        project_id: str,
        spec: SceneSpec,
        events: list[str] | None = None,
        *,
        artifact_version: int | None = None,
        persist_spec: bool = True,
        existing_audio: TTSResult | None = None,
    ) -> ProducedScene:
        events = events if events is not None else []
        base = f"{project_id}/scenes/{spec.scene_id}"
        artifact_version = artifact_version or spec.version
        if persist_spec:
            self._json(f"{base}/spec/v{spec.version}.json", spec)
        relevant = self.corrections.retrieve(
            tags=spec.tags, primitives=[beat.primitive for beat in spec.visual_beats]
        )
        events.extend(["scene.code_started", "scene.audio_started"])
        code_task = generate_scene_code(
            self.llm, spec, [entry.prompt_fragment() for entry in relevant]
        )
        if existing_audio is None:
            audio_path = self.storage.local_path(f"{base}/audio/v{artifact_version}.wav")
            audio_task = self.tts.synthesize(spec.narration, audio_path)
            generated, audio = await asyncio.gather(code_task, audio_task)
        else:
            generated, audio = await code_task, existing_audio
        code_key = f"{base}/code/v{artifact_version}.py"
        if existing_audio is None:
            self._json(f"{base}/audio/v{artifact_version}.json", audio)
        timing = reconcile_timing(spec.duration_target_seconds, audio.duration_seconds)
        self._json(f"{base}/timing/v{artifact_version}.json", timing.__dict__)
        if timing.requires_repair:
            generated = await generate_scene_code(
                self.llm,
                spec,
                [entry.prompt_fragment() for entry in relevant],
                [
                    "Retarget every animation and wait to the actual narration duration of "
                    + f"{audio.duration_seconds:.3f} seconds; do not stretch the final media."
                ],
            )
        self.storage.put_bytes(code_key, generated.python_code.encode())
        feedback: list[str] = []
        report: VerificationReport | None = None
        render_path = ""
        # Best attempt that actually produced a video, kept so a scene whose only
        # remaining faults are cosmetic is not discarded along with its narration.
        fallback: tuple[str, VerificationReport, int] | None = None
        for attempt in range(self.manager.max_repair_attempts + 1):
            render_version = artifact_version + attempt
            events.append("scene.render_started")
            render_dir = self.storage.local_path(f"{base}/render/v{render_version}")
            result = self.renderer.render(generated.python_code, generated.scene_class, render_dir)
            events.append("scene.verifying")
            report = aggregate_reports(
                spec.scene_id,
                {
                    "render": verify_render(spec.scene_id, result),
                    "pedagogy": verify_pedagogy(spec),
                    "layout": _verify_render_layout(spec.scene_id, result),
                    "raw_latex": verify_no_raw_latex_text(spec.scene_id, generated.python_code),
                },
            )
            self._json(f"{base}/verification/v{render_version}.json", report)
            if result.video_path and _only_cosmetic(report):
                fallback = (result.video_path, report, render_version)
            if report.passed and result.video_path:
                render_path = result.video_path
                events.append("scene.complete")
                audio_version = (
                    artifact_version if existing_audio is None else max(1, artifact_version - 1)
                )
                return ProducedScene(
                    spec,
                    str(self.storage.local_path(code_key)),
                    audio,
                    render_path,
                    report,
                    render_version,
                    artifact_version,
                    audio_version,
                )
            action = self.manager.repair_action(attempt + 1)
            if action == RepairAction.BLOCK:
                break
            events.append("scene.repairing")
            feedback = [issue.suggested_repair for issue in report.issues]
            if action in {RepairAction.REVISE_SPEC, RepairAction.SIMPLIFY}:
                beats = (
                    spec.visual_beats[:2] if action == RepairAction.SIMPLIFY else spec.visual_beats
                )
                spec = spec.model_copy(
                    update={
                        "version": spec.version + 1,
                        "visual_beats": beats,
                        "tags": sorted(set(spec.tags + ["simplified"])),
                    }
                )
                self._json(f"{base}/spec/v{spec.version}.json", spec)
            generated = await generate_scene_code(
                self.llm, spec, [entry.prompt_fragment() for entry in relevant], feedback
            )
            code_key = f"{base}/code/v{artifact_version + attempt + 1}.py"
            self.storage.put_bytes(code_key, generated.python_code.encode())
        if fallback is not None:
            # Repairs ran out, but a rendered scene whose only faults are layout
            # placement still teaches its point — and dropping it would silently
            # delete that section's narration and audio from the lesson. Ship it
            # and record the degradation instead.
            fallback_path, fallback_report, fallback_version = fallback
            events.append(f"scene.degraded:{spec.scene_id}")
            return ProducedScene(
                spec,
                str(self.storage.local_path(code_key)),
                audio,
                fallback_path,
                fallback_report,
                fallback_version,
                artifact_version,
                artifact_version if existing_audio is None else max(1, artifact_version - 1),
            )
        raise RuntimeError(
            f"{spec.scene_id} BLOCKED after four repairs: {report.issues if report else 'unknown error'}"
        )
