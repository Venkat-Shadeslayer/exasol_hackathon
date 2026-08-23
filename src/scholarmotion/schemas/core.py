from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


class RequestType(StrEnum):
    CONCEPT_EXPLANATION = "concept_explanation"
    PAPER_EXPLANATION = "paper_explanation"
    PAPER_SECTION_EXPLANATION = "paper_section_explanation"
    EQUATION_EXPLANATION = "equation_explanation"
    FIGURE_EXPLANATION = "figure_explanation"
    RESULTS_EXPLANATION = "results_explanation"
    COMPARISON = "comparison"


class SceneStatus(StrEnum):
    PLANNED = "PLANNED"
    SPEC_READY = "SPEC_READY"
    CODE_GENERATING = "CODE_GENERATING"
    AUDIO_GENERATING = "AUDIO_GENERATING"
    CODE_READY = "CODE_READY"
    AUDIO_READY = "AUDIO_READY"
    TIMING_RECONCILING = "TIMING_RECONCILING"
    DRAFT_RENDERING = "DRAFT_RENDERING"
    VERIFYING = "VERIFYING"
    REPAIRING = "REPAIRING"
    FINAL_RENDERING = "FINAL_RENDERING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class FeedbackType(StrEnum):
    CONTENT_EDIT = "CONTENT_EDIT"
    STYLE_PREFERENCE = "STYLE_PREFERENCE"
    VISUAL_DEFECT = "VISUAL_DEFECT"
    MATH_DEFECT = "MATH_DEFECT"
    SEMANTIC_DEFECT = "SEMANTIC_DEFECT"
    AUDIO_DEFECT = "AUDIO_DEFECT"
    TIMING_DEFECT = "TIMING_DEFECT"
    SCOPE_CHANGE = "SCOPE_CHANGE"


class ArtifactKind(StrEnum):
    PROFILE = "profile"
    RETRIEVAL = "retrieval"
    CURRICULUM = "curriculum"
    PEDAGOGY = "pedagogy"
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    SPEC = "spec"
    CODE = "code"
    AUDIO = "audio"
    TIMING = "timing"
    SUBTITLES = "subtitles"
    RENDER = "render"
    VERIFICATION = "verification"
    VIDEO = "video"
    TIMELINE = "timeline"


class LearnerProfile(BaseModel):
    topic: str
    target_level: str = "general"
    known_concepts: list[str] = Field(default_factory=list)
    unknown_concepts: list[str] = Field(default_factory=list)
    desired_duration_minutes: float = Field(5, gt=0, le=180)
    language: str = "English"
    math_depth: str = "appropriate"
    request_type: RequestType = RequestType.CONCEPT_EXPLANATION


class SourceRef(BaseModel):
    source_id: str
    document_id: str
    page: int | None = None
    section: str | None = None


class KnowledgeCard(BaseModel):
    card_id: str
    kind: Literal["fact", "equation", "example", "definition", "figure"]
    content: str
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(1.0, ge=0, le=1)


class CurriculumItem(BaseModel):
    title: str
    objective: str
    duration_seconds: float = Field(gt=0)
    prerequisites: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class CurriculumPlan(BaseModel):
    topic: str
    items: list[CurriculumItem]
    skipped_known_concepts: list[str] = Field(default_factory=list)


class NarrationBlock(BaseModel):
    block_id: str
    chapter_id: str
    text: str
    learning_objective: str
    source_ids: list[str] = Field(default_factory=list)
    estimated_duration_seconds: float = Field(gt=0)
    defined_symbols: dict[str, str] = Field(default_factory=dict)


class VisualBeat(BaseModel):
    beat_id: str
    narration_segment: str
    visual: str
    primitive: str = "diagram"
    start_seconds: float | None = None
    end_seconds: float | None = None


class LayoutSpec(BaseModel):
    main_visual_region: str = "left"
    equation_region: str = "upper_right"
    subtitle_safe_area: str = "bottom"
    min_text_height: float = 0.3


class SceneSpec(BaseModel):
    scene_id: str
    chapter_id: str
    title: str
    learning_objective: str
    duration_target_seconds: float = Field(gt=0, le=600)
    narration: str
    visual_beats: list[VisualBeat]
    equations: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    start_visual_state: list[str] = Field(default_factory=list)
    end_visual_state: list[str] = Field(default_factory=list)
    layout: LayoutSpec = Field(default_factory=LayoutSpec)
    verification: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    version: int = Field(1, ge=1)


class GeneratedSceneCode(BaseModel):
    scene_class: str
    python_code: str
    assets: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    timing_markers: dict[str, float] = Field(default_factory=dict)


class TimingCue(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> TimingCue:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class TTSResult(BaseModel):
    audio_path: str
    duration_seconds: float = Field(gt=0)
    sentence_timings: list[TimingCue]
    word_timings: list[TimingCue] = Field(default_factory=list)


class VerificationIssue(BaseModel):
    issue_id: str = Field(default_factory=lambda: str(uuid4()))
    scene_id: str
    category: str
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0, le=1)
    frames: list[int] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    description: str
    suggested_repair: str
    correction_memory_candidate: bool = False


class VerificationReport(BaseModel):
    scene_id: str
    passed: bool
    score: float = Field(ge=0, le=1)
    issues: list[VerificationIssue] = Field(default_factory=list)
    layers: dict[str, bool] = Field(default_factory=dict)


class TimelineSegment(BaseModel):
    scene_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    chapter_id: str | None = None
    render_path: str | None = None
    render_version: int = 1

    @model_validator(mode="after")
    def ordered(self) -> TimelineSegment:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Timeline(BaseModel):
    video_id: str
    scenes: list[TimelineSegment]
    duration_seconds: float


class FeedbackClassification(BaseModel):
    feedback_type: FeedbackType
    category: str | None = None
    instruction: str
    memory_eligible: bool = False


class EditRangeRequest(BaseModel):
    video_id: str | None = None
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    instruction: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self) -> EditRangeRequest:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class ParsedEdit(BaseModel):
    operation: Literal["edit_time_range", "edit_scene"] = "edit_time_range"
    start_seconds: float | None = None
    end_seconds: float | None = None
    scene_id: str | None = None
    instruction: str


class ProjectCreate(BaseModel):
    title: str
    request: str
    target_duration_minutes: float = Field(5, gt=0, le=180)
    language: str = "English"


class ProjectRead(ProjectCreate):
    id: UUID
    status: str
    created_at: datetime


class ProgressEvent(BaseModel):
    event: str
    project_id: str
    scene_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
