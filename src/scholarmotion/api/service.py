from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, or_, select

from scholarmotion.agents.feedback_classifier import classify_feedback
from scholarmotion.agents.script_writer import revise_narration
from scholarmotion.config import Settings
from scholarmotion.editing.dependencies import invalidation_for
from scholarmotion.manim_runtime.sandbox import SandboxRenderer
from scholarmotion.media.ffmpeg import FFmpegAssembler
from scholarmotion.media.subtitles import cues_to_srt, cues_to_vtt, merge_scene_cues
from scholarmotion.media.timeline import build_timeline
from scholarmotion.memory.correction_memory import Correction
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import (
    AudioArtifact,
    Chapter,
    CorrectionCandidate,
    CorrectionEntry,
    FeedbackEvent,
    GeneratedCode,
    ModelInvocation,
    ProgressEvent,
    Project,
    ProjectArtifact,
    RenderArtifact,
    Scene,
    SceneSpecification,
    SceneVersion,
    SourceChunk,
    SourceDocument,
    TimelineSegment,
    VerificationIssue,
    VerificationRun,
)
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import (
    create_embedding_provider,
    create_llm_provider,
    create_tts_provider,
)
from scholarmotion.persistence.exasol import ExasolConfig, ExasolUnavailable
from scholarmotion.retrieval.exasol_retrieval import ExasolHybridRetriever
from scholarmotion.retrieval.hybrid_retrieval import (
    HybridRetriever,
    PostgresHybridRetriever,
    RetrievedChunk,
)
from scholarmotion.schemas import ArtifactKind, SceneSpec, TimingCue, TTSResult
from scholarmotion.services import BuildResult, ScholarMotionPipeline

logger = logging.getLogger(__name__)


async def retrieve_context(
    session,
    settings: Settings,
    embeddings,
    project: Project,
    chunk_values: list[dict],
) -> list[RetrievedChunk]:
    """Pick a retrieval backend and return the grounding chunks for a build.

    Exasol is preferred when enabled: it holds the full corpus and scores it
    in-database. If it is unreachable the build degrades to whichever local
    backend the transactional store supports rather than failing outright — a
    lesson grounded by the fallback beats no lesson at all.
    """
    if settings.exasol_enabled:
        try:
            return await ExasolHybridRetriever(
                embeddings, ExasolConfig.from_settings(settings)
            ).search(project.request, project_id=str(project.id), limit=20)
        except ExasolUnavailable as error:
            logger.warning("Exasol retrieval unavailable, falling back: %s", error)
    if not chunk_values:
        return []
    if session.bind and session.bind.dialect.name == "postgresql":
        return await PostgresHybridRetriever(embeddings).search(
            session, project.request, project_id=str(project.id), limit=20
        )
    return await HybridRetriever(embeddings).search(project.request, chunk_values, limit=20)


def create_pipeline(settings: Settings) -> ScholarMotionPipeline:
    return ScholarMotionPipeline(
        llm=create_llm_provider(settings),
        tts=create_tts_provider(settings),
        embeddings=create_embedding_provider(settings),
        storage=LocalObjectStore(settings.object_storage_root),
        # Never substitute text placeholders for production media.  A missing
        # renderer/assembler must surface as a failed build rather than a file
        # called `.mp4` that no player can open.
        renderer=SandboxRenderer(
            settings.manim_binary, settings.render_timeout_seconds, mock_missing=False
        ),
        assembler=FFmpegAssembler(settings.ffmpeg_binary, mock_missing=False),
        max_concurrent_scenes=settings.max_concurrent_scenes,
    )


async def record_event(
    session,
    project_id: UUID,
    event: str,
    *,
    scene_id: UUID | None = None,
    detail: dict | None = None,
) -> None:
    session.add(
        ProgressEvent(project_id=project_id, scene_id=scene_id, event=event, detail=detail or {})
    )


async def record_correction_evidence(
    session,
    runtime: ScholarMotionPipeline,
    *,
    category: str,
    trigger: str,
    recommended_fix: str,
    evidence: dict,
) -> None:
    candidate = await session.scalar(
        select(CorrectionCandidate)
        .where(
            CorrectionCandidate.category == category,
            CorrectionCandidate.trigger_conditions == trigger,
            CorrectionCandidate.status == "candidate",
        )
        .order_by(CorrectionCandidate.created_at.desc())
    )
    if candidate is None:
        session.add(
            CorrectionCandidate(
                category=category,
                trigger_conditions=trigger,
                recommended_fix=recommended_fix,
                evidence_count=1,
                confidence=0.75,
                evidence=[evidence],
            )
        )
        return
    candidate.evidence_count += 1
    candidate.confidence = min(0.98, candidate.confidence + 0.08)
    candidate.evidence = [*candidate.evidence, evidence]
    if candidate.evidence_count < 2 or candidate.confidence < 0.8:
        return
    sequence = (await session.scalar(select(func.count(CorrectionEntry.id))) or 0) + 1
    correction_key = f"CORR-LEARNED-{sequence:04d}"
    entry = CorrectionEntry(
        correction_key=correction_key,
        category=category,
        trigger_conditions=trigger,
        anti_pattern=f"Repeat the observed {category} failure.",
        required_behavior=f"Prevent {category} and preserve assigned safe regions.",
        recommended_fix=recommended_fix,
        evidence_count=candidate.evidence_count,
        confidence=candidate.confidence,
        tags=[category],
        applicable_model="any",
        validation_tests=["automatic_repair_verification"],
    )
    session.add(entry)
    candidate.status = "promoted"
    correction = Correction(
        correction_id=correction_key,
        category=category,
        trigger_conditions=trigger,
        anti_pattern=entry.anti_pattern,
        required_behavior=entry.required_behavior,
        recommended_fix=recommended_fix,
        evidence_count=candidate.evidence_count,
        confidence=candidate.confidence,
        tags=(category,),
    )
    try:
        runtime.corrections.append_validated(
            correction, validation_tests=["automatic_repair_verification"]
        )
    except OSError as exc:
        # The database remains authoritative when a deployment mounts knowledge read-only.
        entry.validation_tests = [
            *entry.validation_tests,
            f"markdown_sync_failed:{type(exc).__name__}",
        ]


async def run_generation(database: Database, settings: Settings, project_id: UUID) -> BuildResult:
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise KeyError(str(project_id))
        project.status = "GENERATING"
        await record_event(session, project_id, "project.started")
        await session.commit()
        try:
            source_rows = (
                await session.execute(
                    select(SourceChunk, SourceDocument)
                    .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
                    .where(
                        or_(
                            SourceDocument.project_id == project_id,
                            SourceDocument.project_id.is_(None),
                        )
                    )
                )
            ).all()
            chunk_values = [
                {
                    "id": str(chunk.id),
                    "document_id": str(document.id),
                    "document_kind": document.kind,
                    "class_level": chunk.class_level,
                    "subject": chunk.subject,
                    "chapter": chunk.chapter,
                    "section": chunk.section,
                    "page": chunk.page,
                    "content_type": chunk.content_type,
                    "text": chunk.text,
                    "concept_tags": chunk.concept_tags,
                }
                for chunk, document in source_rows
            ]
            retrieved_chunks: list[dict] = []
            runtime = create_pipeline(settings)
            retrieved = await retrieve_context(
                session, settings, runtime.embeddings, project, chunk_values
            )
            retrieved_chunks = [{**item.chunk, "score": item.score} for item in retrieved]
            result = await runtime.build(
                str(project_id),
                project.request,
                duration_minutes=project.target_duration_minutes,
                language=project.language,
                retrieved_chunks=retrieved_chunks,
            )
            chapter_rows: dict[str, Chapter] = {}
            for spec in [item.spec for item in result.scenes]:
                if spec.chapter_id not in chapter_rows:
                    chapter = Chapter(
                        project_id=project_id,
                        chapter_key=spec.chapter_id,
                        title=spec.title,
                        order_index=len(chapter_rows) + 1,
                    )
                    session.add(chapter)
                    await session.flush()
                    chapter_rows[spec.chapter_id] = chapter
            scene_rows: list[Scene] = []
            for kind in (
                ArtifactKind.PROFILE,
                ArtifactKind.RETRIEVAL,
                ArtifactKind.CURRICULUM,
                ArtifactKind.PEDAGOGY,
                ArtifactKind.SCRIPT,
                ArtifactKind.STORYBOARD,
            ):
                session.add(
                    ProjectArtifact(
                        project_id=project_id,
                        kind=kind.value,
                        version=1,
                        uri=f"{project_id}/artifacts/{kind.value}/v1.json",
                        payload={},
                    )
                )
            for order, produced in enumerate(result.scenes, 1):
                spec = produced.spec
                scene = Scene(
                    project_id=project_id,
                    chapter_id=chapter_rows[spec.chapter_id].id,
                    scene_key=spec.scene_id,
                    title=spec.title,
                    order_index=order,
                    status="COMPLETE",
                    active_spec_version=spec.version,
                    active_code_version=produced.code_version,
                    active_audio_version=produced.audio_version,
                    active_render_version=produced.render_version,
                    duration=produced.audio.duration_seconds,
                    verification_state="PASSED",
                )
                session.add(scene)
                await session.flush()
                scene_rows.append(scene)
                base = f"{project_id}/scenes/{spec.scene_id}"
                session.add_all(
                    [
                        SceneVersion(
                            scene_id=scene.id,
                            version=1,
                            reason="initial",
                            snapshot={
                                "spec_version": spec.version,
                                "code_version": produced.code_version,
                                "audio_version": produced.audio_version,
                                "render_version": produced.render_version,
                            },
                        ),
                        SceneSpecification(
                            scene_id=scene.id,
                            version=spec.version,
                            payload=spec.model_dump(mode="json"),
                            uri=f"{base}/spec/v{spec.version}.json",
                        ),
                        GeneratedCode(
                            scene_id=scene.id,
                            version=produced.code_version,
                            spec_version=spec.version,
                            uri=str(Path(produced.code_path)),
                            scene_class=f"Scene{spec.scene_id}",
                        ),
                        AudioArtifact(
                            scene_id=scene.id,
                            version=produced.audio_version,
                            narration_version=spec.version,
                            uri=produced.audio.audio_path,
                            duration=produced.audio.duration_seconds,
                            timings=[cue.model_dump() for cue in produced.audio.sentence_timings],
                        ),
                        RenderArtifact(
                            scene_id=scene.id,
                            version=produced.render_version,
                            code_version=produced.code_version,
                            audio_version=produced.audio_version,
                            uri=produced.render_path,
                            metadata_json={},
                        ),
                        ModelInvocation(
                            project_id=project_id,
                            agent="scene_code_generator",
                            provider=settings.main_llm_provider,
                            model=settings.main_llm_model,
                            prompt_version="scene-code-v1",
                            input_artifact_ids=[f"{scene.scene_key}:spec:v{spec.version}"],
                            output_artifact_id=f"{scene.scene_key}:code:v{produced.code_version}",
                            temperature=settings.main_llm_temperature,
                            latency_ms=0,
                        ),
                    ]
                )
                verification = VerificationRun(
                    scene_id=scene.id,
                    render_version=produced.render_version,
                    passed=produced.report.passed,
                    score=produced.report.score,
                    layers=produced.report.layers,
                )
                session.add(verification)
                await session.flush()
                for issue in produced.report.issues:
                    session.add(
                        VerificationIssue(
                            run_id=verification.id,
                            scene_id=scene.id,
                            category=issue.category,
                            severity=issue.severity,
                            confidence=issue.confidence,
                            payload=issue.model_dump(mode="json"),
                        )
                    )
                    if issue.correction_memory_candidate:
                        await record_correction_evidence(
                            session,
                            runtime,
                            category=issue.category,
                            trigger=issue.description,
                            recommended_fix=issue.suggested_repair,
                            evidence={"scene_id": spec.scene_id, "source": "verifier"},
                        )
                await record_event(
                    session,
                    project_id,
                    "scene.complete",
                    scene_id=scene.id,
                    detail={"scene_id": spec.scene_id},
                )
            for segment, scene in zip(result.timeline.scenes, scene_rows):
                scene.timeline_start, scene.timeline_end = segment.start, segment.end
                session.add(
                    TimelineSegment(
                        project_id=project_id,
                        video_version=1,
                        scene_id=scene.id,
                        start_seconds=segment.start,
                        end_seconds=segment.end,
                        render_version=segment.render_version,
                    )
                )
            session.add_all(
                [
                    ProjectArtifact(
                        project_id=project_id,
                        kind=ArtifactKind.VIDEO.value,
                        version=1,
                        uri=result.video_path,
                        payload={
                            "subtitles_srt": result.srt_path,
                            "subtitles_vtt": result.vtt_path,
                        },
                    ),
                    ProjectArtifact(
                        project_id=project_id,
                        kind=ArtifactKind.TIMELINE.value,
                        version=1,
                        uri=f"{project_id}/video/v1/timeline.json",
                        payload=result.timeline.model_dump(mode="json"),
                    ),
                ]
            )
            project.active_video_path = result.video_path
            project.status = "COMPLETE"
            await record_event(session, project_id, "video.complete")
            await session.commit()
            return result
        except Exception as exc:
            project.status = "FAILED"
            await record_event(session, project_id, "project.failed", detail={"error": str(exc)})
            await session.commit()
            raise


async def edit_range(
    database: Database,
    settings: Settings,
    project_id: UUID,
    start: float,
    end: float,
    instruction: str,
) -> list[str]:
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise KeyError(str(project_id))
        scenes = list(
            (
                await session.scalars(
                    select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
                )
            ).all()
        )
        affected = [
            scene
            for scene in scenes
            if scene.timeline_start is not None
            and scene.timeline_end is not None
            and scene.timeline_start < end
            and scene.timeline_end > start
        ]
        if not affected:
            raise ValueError("selected range does not intersect any scene")
        classifications = classify_feedback(instruction)
        classification = classifications[0]
        plan = invalidation_for(classification.feedback_type, instruction)
        runtime = create_pipeline(settings)
        affected_keys: list[str] = []
        for scene in affected:
            affected_keys.append(scene.scene_key)
            spec_row = await session.scalar(
                select(SceneSpecification).where(
                    SceneSpecification.scene_id == scene.id, SceneSpecification.active.is_(True)
                )
            )
            if spec_row is None:
                raise RuntimeError(f"active SceneSpec missing for {scene.scene_key}")
            spec = SceneSpec.model_validate(spec_row.payload)
            narration_changed = plan.regenerate_audio
            if plan.root in {
                ArtifactKind.PROFILE,
                ArtifactKind.PEDAGOGY,
                ArtifactKind.SCRIPT,
                ArtifactKind.STORYBOARD,
                ArtifactKind.SPEC,
            }:
                spec_row.active = False
                # Rewrite the narration to answer the request. Appending the
                # instruction instead would make the speech engine read the
                # request itself aloud to the student.
                revised_narration = spec.narration
                if narration_changed:
                    revised_narration = await revise_narration(
                        runtime.llm,
                        spec.narration,
                        instruction,
                        learning_objective=spec.learning_objective or "",
                    )
                spec = spec.model_copy(
                    update={
                        "version": spec.version + 1,
                        "narration": revised_narration,
                        "tags": sorted(set(spec.tags + ["user_edit"])),
                    }
                )
                persist_spec = True
            else:
                persist_spec = False
            artifact_version = (
                max(
                    scene.active_code_version,
                    scene.active_audio_version,
                    scene.active_render_version,
                )
                + 1
            )
            existing_audio = None
            if not narration_changed:
                audio_row = await session.scalar(
                    select(AudioArtifact).where(
                        AudioArtifact.scene_id == scene.id, AudioArtifact.active.is_(True)
                    )
                )
                if audio_row:
                    existing_audio = TTSResult(
                        audio_path=audio_row.uri,
                        duration_seconds=audio_row.duration,
                        sentence_timings=audio_row.timings,
                        word_timings=[],
                    )
            produced = await runtime.produce_scene(
                str(project_id),
                spec,
                artifact_version=artifact_version,
                persist_spec=persist_spec,
                existing_audio=existing_audio,
            )
            old_code = await session.scalar(
                select(GeneratedCode).where(
                    GeneratedCode.scene_id == scene.id, GeneratedCode.active.is_(True)
                )
            )
            old_render = await session.scalar(
                select(RenderArtifact).where(
                    RenderArtifact.scene_id == scene.id, RenderArtifact.active.is_(True)
                )
            )
            if old_code:
                old_code.active, old_code.stale = False, True
            if old_render:
                old_render.active, old_render.stale = False, True
            if narration_changed:
                old_audio = await session.scalar(
                    select(AudioArtifact).where(
                        AudioArtifact.scene_id == scene.id, AudioArtifact.active.is_(True)
                    )
                )
                if old_audio:
                    old_audio.active, old_audio.stale = False, True
                session.add(
                    AudioArtifact(
                        scene_id=scene.id,
                        version=artifact_version,
                        narration_version=spec.version,
                        uri=produced.audio.audio_path,
                        duration=produced.audio.duration_seconds,
                        timings=[cue.model_dump() for cue in produced.audio.sentence_timings],
                    )
                )
                scene.active_audio_version = artifact_version
                scene.duration = produced.audio.duration_seconds
            if persist_spec:
                session.add(
                    SceneSpecification(
                        scene_id=scene.id,
                        version=spec.version,
                        payload=spec.model_dump(mode="json"),
                        uri=f"{project_id}/scenes/{scene.scene_key}/spec/v{spec.version}.json",
                    )
                )
                scene.active_spec_version = spec.version
            session.add(
                GeneratedCode(
                    scene_id=scene.id,
                    version=artifact_version,
                    spec_version=spec.version,
                    uri=produced.code_path,
                    scene_class=f"Scene{scene.scene_key}",
                )
            )
            session.add(
                RenderArtifact(
                    scene_id=scene.id,
                    version=produced.render_version,
                    code_version=artifact_version,
                    audio_version=scene.active_audio_version,
                    uri=produced.render_path,
                    metadata_json={"edit": instruction},
                )
            )
            scene.active_code_version = artifact_version
            scene.active_render_version = produced.render_version
            scene.status = "COMPLETE"
            for previous in (
                await session.scalars(
                    select(SceneVersion).where(
                        SceneVersion.scene_id == scene.id, SceneVersion.active.is_(True)
                    )
                )
            ).all():
                previous.active = False
            next_version = (
                await session.scalar(
                    select(func.max(SceneVersion.version)).where(SceneVersion.scene_id == scene.id)
                )
                or 0
            ) + 1
            session.add(
                SceneVersion(
                    scene_id=scene.id,
                    version=next_version,
                    reason=instruction,
                    snapshot={
                        "spec_version": scene.active_spec_version,
                        "code_version": scene.active_code_version,
                        "audio_version": scene.active_audio_version,
                        "render_version": scene.active_render_version,
                    },
                )
            )
            for result in classifications:
                session.add(
                    FeedbackEvent(
                        project_id=project_id,
                        scene_ids=[scene.scene_key],
                        feedback_type=result.feedback_type.value,
                        category=result.category,
                        instruction=instruction,
                        range_start=start,
                        range_end=end,
                    )
                )
                if result.memory_eligible:
                    await record_correction_evidence(
                        session,
                        runtime,
                        category=result.category or "feedback",
                        trigger=instruction,
                        recommended_fix=f"Prevent {result.category} for matching scene tags.",
                        evidence={"scene_id": scene.scene_key, "source": "user_feedback"},
                    )
        await session.flush()
        render_paths: list[str] = []
        audio_paths: list[str] = []
        for scene in scenes:
            render = await session.scalar(
                select(RenderArtifact).where(
                    RenderArtifact.scene_id == scene.id, RenderArtifact.active.is_(True)
                )
            )
            audio = await session.scalar(
                select(AudioArtifact).where(
                    AudioArtifact.scene_id == scene.id, AudioArtifact.active.is_(True)
                )
            )
            if render is None or audio is None:
                raise RuntimeError(f"active media missing for {scene.scene_key}")
            render_paths.append(render.uri)
            audio_paths.append(audio.uri)
        video_version = (
            await session.scalar(
                select(func.max(ProjectArtifact.version)).where(
                    ProjectArtifact.project_id == project_id,
                    ProjectArtifact.kind == ArtifactKind.VIDEO.value,
                )
            )
            or 0
        ) + 1
        output_path = runtime.storage.local_path(f"{project_id}/video/v{video_version}/final.mp4")
        assembly = runtime.assembler.assemble(render_paths, output_path, audio_paths=audio_paths)
        if not assembly.success:
            raise RuntimeError(assembly.log[-1000:])
        timeline = build_timeline(
            [
                (scene.scene_key, scene.duration, scene.active_render_version, path)
                for scene, path in zip(scenes, render_paths)
            ]
        )
        runtime._json(f"{project_id}/video/v{video_version}/timeline.json", timeline)
        for old in (
            await session.scalars(
                select(ProjectArtifact).where(
                    ProjectArtifact.project_id == project_id,
                    ProjectArtifact.kind.in_(
                        [ArtifactKind.VIDEO.value, ArtifactKind.TIMELINE.value]
                    ),
                    ProjectArtifact.active.is_(True),
                )
            )
        ).all():
            old.active = False
        session.add_all(
            [
                ProjectArtifact(
                    project_id=project_id,
                    kind=ArtifactKind.VIDEO.value,
                    version=video_version,
                    uri=str(output_path),
                    payload={"assembly_inputs": assembly.ordered_inputs},
                ),
                ProjectArtifact(
                    project_id=project_id,
                    kind=ArtifactKind.TIMELINE.value,
                    version=video_version,
                    uri=f"{project_id}/video/v{video_version}/timeline.json",
                    payload=timeline.model_dump(mode="json"),
                ),
            ]
        )
        for segment, scene in zip(timeline.scenes, scenes):
            scene.timeline_start, scene.timeline_end = segment.start, segment.end
            session.add(
                TimelineSegment(
                    project_id=project_id,
                    video_version=video_version,
                    scene_id=scene.id,
                    start_seconds=segment.start,
                    end_seconds=segment.end,
                    render_version=scene.active_render_version,
                )
            )
        project.active_video_path = str(output_path)
        await record_event(
            session, project_id, "video.complete", detail={"selectively_regenerated": affected_keys}
        )
        await session.commit()
        return affected_keys


async def reassemble_active(database: Database, settings: Settings, project_id: UUID) -> int:
    """Assemble a new immutable video version from the selected scene versions."""
    async with database.sessions() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise KeyError(str(project_id))
        scenes = list(
            (
                await session.scalars(
                    select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
                )
            ).all()
        )
        render_paths: list[str] = []
        audio_paths: list[str] = []
        scene_cues: list[tuple[float, list]] = []
        cursor = 0.0
        for scene in scenes:
            render = await session.scalar(
                select(RenderArtifact).where(
                    RenderArtifact.scene_id == scene.id,
                    RenderArtifact.version == scene.active_render_version,
                )
            )
            audio = await session.scalar(
                select(AudioArtifact).where(
                    AudioArtifact.scene_id == scene.id,
                    AudioArtifact.version == scene.active_audio_version,
                )
            )
            if render is None or audio is None:
                raise RuntimeError(f"active media missing for {scene.scene_key}")
            scene.duration = audio.duration
            render_paths.append(render.uri)
            audio_paths.append(audio.uri)
            scene_cues.append((cursor, [TimingCue.model_validate(cue) for cue in audio.timings]))
            cursor += audio.duration
        video_version = (
            await session.scalar(
                select(func.max(ProjectArtifact.version)).where(
                    ProjectArtifact.project_id == project_id,
                    ProjectArtifact.kind == ArtifactKind.VIDEO.value,
                )
            )
            or 0
        ) + 1
        runtime = create_pipeline(settings)
        output_path = runtime.storage.local_path(f"{project_id}/video/v{video_version}/final.mp4")
        assembly = runtime.assembler.assemble(render_paths, output_path, audio_paths=audio_paths)
        if not assembly.success:
            raise RuntimeError(assembly.log[-1000:])
        timeline = build_timeline(
            [
                (scene.scene_key, scene.duration, scene.active_render_version, path)
                for scene, path in zip(scenes, render_paths)
            ]
        )
        runtime._json(f"{project_id}/video/v{video_version}/timeline.json", timeline)
        cues = merge_scene_cues(scene_cues)
        srt_key = f"{project_id}/video/v{video_version}/subtitles.srt"
        vtt_key = f"{project_id}/video/v{video_version}/subtitles.vtt"
        runtime.storage.put_bytes(srt_key, cues_to_srt(cues).encode())
        runtime.storage.put_bytes(vtt_key, cues_to_vtt(cues).encode())
        for old in (
            await session.scalars(
                select(ProjectArtifact).where(
                    ProjectArtifact.project_id == project_id,
                    ProjectArtifact.kind.in_(
                        [ArtifactKind.VIDEO.value, ArtifactKind.TIMELINE.value]
                    ),
                    ProjectArtifact.active.is_(True),
                )
            )
        ).all():
            old.active = False
        session.add_all(
            [
                ProjectArtifact(
                    project_id=project_id,
                    kind=ArtifactKind.VIDEO.value,
                    version=video_version,
                    uri=str(output_path),
                    payload={
                        "assembly_inputs": assembly.ordered_inputs,
                        "subtitles_srt": str(runtime.storage.local_path(srt_key)),
                        "subtitles_vtt": str(runtime.storage.local_path(vtt_key)),
                    },
                ),
                ProjectArtifact(
                    project_id=project_id,
                    kind=ArtifactKind.TIMELINE.value,
                    version=video_version,
                    uri=f"{project_id}/video/v{video_version}/timeline.json",
                    payload=timeline.model_dump(mode="json"),
                ),
            ]
        )
        for segment, scene in zip(timeline.scenes, scenes):
            scene.timeline_start, scene.timeline_end = segment.start, segment.end
            session.add(
                TimelineSegment(
                    project_id=project_id,
                    video_version=video_version,
                    scene_id=scene.id,
                    start_seconds=segment.start,
                    end_seconds=segment.end,
                    render_version=scene.active_render_version,
                )
            )
        project.active_video_path = str(output_path)
        await record_event(session, project_id, "video.complete", detail={"restored_version": True})
        await session.commit()
        return video_version
