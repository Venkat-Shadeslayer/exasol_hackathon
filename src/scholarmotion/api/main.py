from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scholarmotion.api.service import edit_range as edit_project_range
from scholarmotion.api.service import reassemble_active, run_generation
from scholarmotion.config import get_settings
from scholarmotion.media.timeline import parse_edit_instruction
from scholarmotion.persistence.database import Database
from scholarmotion.persistence.models import (
    AudioArtifact,
    CorrectionEntry,
    GeneratedCode,
    ProgressEvent,
    Project,
    RenderArtifact,
    Scene,
    SceneSpecification,
    SceneVersion,
    SourceChunk,
    SourceDocument,
    TimelineSegment,
)
from scholarmotion.persistence.exasol import (
    ExasolConfig,
    ExasolUnavailable,
    bootstrap_async,
)
from scholarmotion.persistence.storage import LocalObjectStore
from scholarmotion.providers import create_embedding_provider
from scholarmotion.retrieval.exasol_retrieval import ExasolHybridRetriever
from scholarmotion.retrieval.paper_ingestion import PaperParser
from scholarmotion.schemas import EditRangeRequest, ProjectCreate

settings = get_settings()
database = Database(settings.database_url)
store = LocalObjectStore(settings.object_storage_root)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.create_all()
    yield
    await database.close()


app = FastAPI(title="ScholarMotion API", version="0.1.0", lifespan=lifespan)


async def session_dependency():
    async with database.sessions() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
SourceFile = Annotated[UploadFile, File()]


def queue_range_edit(
    background: BackgroundTasks,
    project_id: UUID,
    start: float,
    end: float,
    instruction: str,
) -> str | None:
    if settings.database_url.startswith("postgresql") and not settings.celery_task_always_eager:
        from scholarmotion.tasks.editing import edit_project_range as edit_task

        return edit_task.apply_async(
            args=[str(project_id), start, end, instruction], queue="feedback"
        ).id
    background.add_task(edit_project_range, database, settings, project_id, start, end, instruction)
    return None


@app.get("/health")
async def health():
    return {"status": "ok", "mode": settings.main_llm_provider}


@app.post("/projects", status_code=201)
async def create_project(payload: ProjectCreate, session: SessionDep):
    project = Project(**payload.model_dump())
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return _project(project)


@app.get("/projects")
async def list_projects(session: SessionDep):
    return [
        _project(item)
        for item in (
            await session.scalars(select(Project).order_by(Project.created_at.desc()))
        ).all()
    ]


@app.get("/projects/{project_id}")
async def get_project(project_id: UUID, session: SessionDep):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    return _project(project)


@app.post("/projects/{project_id}/sources", status_code=201)
async def upload_source(
    project_id: UUID,
    file: SourceFile,
    session: SessionDep,
):
    if not await session.get(Project, project_id):
        raise HTTPException(404, "project not found")
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(413, "source exceeds 100 MB")
    key = f"{project_id}/sources/{file.filename or 'source.pdf'}"
    try:
        store.put_bytes(key, data)
    except FileExistsError:
        raise HTTPException(409, "a source with this name already exists")
    document = SourceDocument(
        project_id=project_id,
        kind="paper",
        title=Path(file.filename or "source").stem,
        uri=key,
        metadata_json={"content_type": file.content_type},
    )
    session.add(document)
    await session.flush()
    if (file.filename or "").lower().endswith(".pdf"):
        try:
            paper = PaperParser().parse(
                store.local_path(key),
                asset_dir=store.local_path(f"{project_id}/sources/assets/{document.id}"),
            )
            vectors = await create_embedding_provider(settings).embed(
                [chunk.text for chunk in paper.chunks]
            )
            document.title = paper.title
            document.authors = paper.authors
            document.metadata_json = {
                **document.metadata_json,
                **paper.metadata,
                "abstract": paper.abstract,
                "ingestion_status": "complete",
            }
            for chunk, embedding in zip(paper.chunks, vectors):
                session.add(
                    SourceChunk(
                        document_id=document.id,
                        section=chunk.section,
                        page=chunk.page,
                        content_type=chunk.content_type,
                        text=chunk.text,
                        equations=[chunk.text] if chunk.content_type == "equation" else [],
                        concept_tags=[],
                        prerequisite_tags=[],
                        embedding=embedding,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - preserve source and surface provider/parser errors
            document.metadata_json = {
                **document.metadata_json,
                "ingestion_status": "failed",
                "ingestion_error": str(exc),
            }
    await session.commit()
    await session.refresh(document)
    return {"id": str(document.id), "title": document.title, "uri": document.uri}


@app.post("/projects/{project_id}/generate", status_code=202)
async def generate(
    project_id: UUID,
    background: BackgroundTasks,
    session: SessionDep,
):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    if project.status == "GENERATING":
        raise HTTPException(409, "generation already running")
    project.status = "GENERATING"
    await session.commit()
    if settings.database_url.startswith("postgresql") and not settings.celery_task_always_eager:
        from scholarmotion.tasks.planning import build_project

        task = build_project.apply_async(args=[str(project_id)], queue="planning")
        return {"project_id": str(project_id), "status": "queued", "task_id": task.id}
    background.add_task(run_generation, database, settings, project_id)
    return {"project_id": str(project_id), "status": "queued", "task_id": None}


@app.get("/projects/{project_id}/progress")
async def progress(project_id: UUID, session: SessionDep):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    events = (
        await session.scalars(
            select(ProgressEvent)
            .where(ProgressEvent.project_id == project_id)
            .order_by(ProgressEvent.created_at)
        )
    ).all()
    return {
        "status": project.status,
        "events": [
            {
                "event": event.event,
                "scene_id": str(event.scene_id) if event.scene_id else None,
                "detail": event.detail,
                "created_at": event.created_at,
            }
            for event in events
        ],
    }


@app.get("/projects/{project_id}/scenes")
async def scenes(project_id: UUID, session: SessionDep):
    values = (
        await session.scalars(
            select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
        )
    ).all()
    return [_scene(item) for item in values]


@app.get("/projects/{project_id}/video")
async def video(project_id: UUID, session: SessionDep):
    project = await session.get(Project, project_id)
    if not project or not project.active_video_path:
        raise HTTPException(404, "video not ready")
    return FileResponse(
        project.active_video_path, media_type="video/mp4", filename=f"{project.title}.mp4"
    )


@app.get("/projects/{project_id}/timeline")
async def timeline(project_id: UUID, session: SessionDep):
    version = await session.scalar(
        select(func.max(TimelineSegment.video_version)).where(
            TimelineSegment.project_id == project_id
        )
    )
    values = (
        await session.scalars(
            select(TimelineSegment)
            .where(
                TimelineSegment.project_id == project_id, TimelineSegment.video_version == version
            )
            .order_by(TimelineSegment.start_seconds)
        )
    ).all()
    return {
        "video_version": version,
        "scenes": [
            {
                "scene_id": str(item.scene_id),
                "start": item.start_seconds,
                "end": item.end_seconds,
                "render_version": item.render_version,
            }
            for item in values
        ],
    }


@app.post("/projects/{project_id}/feedback", status_code=202)
async def feedback(project_id: UUID, payload: dict, background: BackgroundTasks):
    try:
        parsed = parse_edit_instruction(payload.get("message", ""))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if parsed.operation == "edit_scene":
        raise HTTPException(422, "use the scene edit endpoint for scene-only instructions")
    task_id = queue_range_edit(
        background,
        project_id,
        parsed.start_seconds,
        parsed.end_seconds,
        parsed.instruction,
    )
    return {**parsed.model_dump(), "task_id": task_id}


@app.post("/projects/{project_id}/edit-range", status_code=202)
async def edit_range(project_id: UUID, payload: EditRangeRequest, background: BackgroundTasks):
    task_id = queue_range_edit(
        background,
        project_id,
        payload.start_time,
        payload.end_time,
        payload.instruction,
    )
    return {
        "status": "queued",
        "range": [payload.start_time, payload.end_time],
        "task_id": task_id,
    }


@app.post("/projects/{project_id}/scenes/{scene_id}/edit", status_code=202)
async def edit_scene(
    project_id: UUID,
    scene_id: str,
    payload: dict,
    background: BackgroundTasks,
    session: SessionDep,
):
    scene = await session.scalar(
        select(Scene).where(Scene.project_id == project_id, Scene.scene_key == scene_id)
    )
    if not scene:
        raise HTTPException(404, "scene not found")
    task_id = queue_range_edit(
        background,
        project_id,
        scene.timeline_start or 0,
        scene.timeline_end or scene.duration,
        payload.get("instruction", "regenerate scene"),
    )
    return {"status": "queued", "scene_id": scene_id, "task_id": task_id}


@app.post("/projects/{project_id}/scenes/{scene_id}/regenerate", status_code=202)
async def regenerate_scene(
    project_id: UUID,
    scene_id: str,
    background: BackgroundTasks,
    session: SessionDep,
):
    return await edit_scene(
        project_id,
        scene_id,
        {"instruction": "Regenerate this scene while preserving its content."},
        background,
        session,
    )


@app.get("/projects/{project_id}/scenes/{scene_id}/versions")
async def versions(project_id: UUID, scene_id: str, session: SessionDep):
    scene = await session.scalar(
        select(Scene).where(Scene.project_id == project_id, Scene.scene_key == scene_id)
    )
    if not scene:
        raise HTTPException(404, "scene not found")
    values = (
        await session.scalars(
            select(SceneVersion)
            .where(SceneVersion.scene_id == scene.id)
            .order_by(SceneVersion.version.desc())
        )
    ).all()
    return [
        {
            "version": item.version,
            "reason": item.reason,
            "active": item.active,
            "snapshot": item.snapshot,
            "created_at": item.created_at,
        }
        for item in values
    ]


@app.post("/projects/{project_id}/scenes/{scene_id}/restore/{version}")
async def restore_version(
    project_id: UUID,
    scene_id: str,
    version: int,
    session: SessionDep,
):
    scene = await session.scalar(
        select(Scene).where(Scene.project_id == project_id, Scene.scene_key == scene_id)
    )
    if not scene:
        raise HTTPException(404, "scene not found")
    target = await session.scalar(
        select(SceneVersion).where(
            SceneVersion.scene_id == scene.id, SceneVersion.version == version
        )
    )
    if not target:
        raise HTTPException(404, "version not found")
    for item in (
        await session.scalars(select(SceneVersion).where(SceneVersion.scene_id == scene.id))
    ).all():
        item.active = item.id == target.id
    snapshot = target.snapshot
    scene.active_spec_version = snapshot["spec_version"]
    scene.active_code_version = snapshot["code_version"]
    scene.active_audio_version = snapshot["audio_version"]
    scene.active_render_version = snapshot["render_version"]
    versioned_models = (
        (SceneSpecification, snapshot["spec_version"]),
        (GeneratedCode, snapshot["code_version"]),
        (AudioArtifact, snapshot["audio_version"]),
        (RenderArtifact, snapshot["render_version"]),
    )
    for model, active_version in versioned_models:
        for artifact in (
            await session.scalars(select(model).where(model.scene_id == scene.id))
        ).all():
            artifact.active = artifact.version == active_version
            artifact.stale = artifact.version != active_version
    await session.commit()
    video_version = await reassemble_active(database, settings, project_id)
    return {
        "restored": version,
        "scene_id": scene_id,
        "video_version": video_version,
        "reassembly_required": False,
    }


@app.get("/corpus/analytics")
async def corpus_analytics():
    """Corpus coverage aggregated in Exasol.

    Surfaces what the knowledge base can actually ground: volume per
    class/subject/chapter, the equation/definition mix, and chapters too thin to
    teach from. These are full-corpus scans, which is why they run on the
    analytical platform rather than the transactional store.
    """
    if not settings.exasol_enabled:
        raise HTTPException(503, "Exasol is not enabled; set EXASOL_ENABLED=true")
    retriever = ExasolHybridRetriever(
        create_embedding_provider(settings), ExasolConfig.from_settings(settings)
    )
    try:
        return await retriever.corpus_analytics()
    except ExasolUnavailable as error:
        raise HTTPException(503, str(error)) from error


@app.get("/corpus/health")
async def corpus_health():
    """Report whether Exasol is reachable and which scoring path is live."""
    if not settings.exasol_enabled:
        return {"enabled": False, "reachable": False, "scoring": None}
    try:
        status = await bootstrap_async(ExasolConfig.from_settings(settings))
    except ExasolUnavailable as error:
        return {"enabled": True, "reachable": False, "error": str(error)}
    return {
        "enabled": True,
        "reachable": True,
        "schema": status["schema"],
        "scoring": "udf" if status["udf_ready"] else "sql",
        "udf_error": status["udf_error"],
    }


@app.get("/corrections")
async def corrections(session: SessionDep):
    values = (
        await session.scalars(select(CorrectionEntry).order_by(CorrectionEntry.confidence.desc()))
    ).all()
    return [
        {
            "id": item.correction_key,
            "category": item.category,
            "confidence": item.confidence,
            "evidence_count": item.evidence_count,
        }
        for item in values
    ]


def _project(item: Project) -> dict:
    return {
        "id": str(item.id),
        "title": item.title,
        "request": item.request,
        "target_duration_minutes": item.target_duration_minutes,
        "language": item.language,
        "status": item.status,
        "video_path": item.active_video_path,
        "created_at": item.created_at,
    }


def _scene(item: Scene) -> dict:
    return {
        "id": str(item.id),
        "scene_id": item.scene_key,
        "title": item.title,
        "order": item.order_index,
        "status": item.status,
        "duration": item.duration,
        "start": item.timeline_start,
        "end": item.timeline_end,
        "versions": {
            "spec": item.active_spec_version,
            "code": item.active_code_version,
            "audio": item.active_audio_version,
            "render": item.active_render_version,
        },
        "verification": item.verification_state,
        "repair_attempts": item.repair_attempts,
    }


def run() -> None:
    import uvicorn

    uvicorn.run("scholarmotion.api.main:app", host="0.0.0.0", port=8000, reload=False)
