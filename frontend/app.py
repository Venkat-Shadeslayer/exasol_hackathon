"""Local Streamlit interface for ScholarMotion's FastAPI service."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import streamlit as st


API_URL = os.getenv("SCHOLARMOTION_API_URL", "http://127.0.0.1:8000").rstrip("/")


def api(method: str, path: str, **kwargs: Any) -> Any:
    response = httpx.request(method, f"{API_URL}{path}", timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


st.set_page_config(page_title="ScholarMotion", page_icon="🎓", layout="wide")
st.title("ScholarMotion")
st.caption("Generate a narrated Manim lesson with Gemini and locally hosted Kokoro-82M speech.")

try:
    health = api("GET", "/health")
    st.sidebar.success(f"API connected · LLM: {health['mode']}")
except httpx.HTTPError as error:
    st.error(f"Cannot connect to the API at {API_URL}: {error}")
    st.stop()

with st.sidebar:
    st.header("New lesson")
    with st.form("create-project", clear_on_submit=True):
        title = st.text_input("Title", placeholder="Visual introduction to eigenvectors")
        request = st.text_area(
            "What should the lesson teach?",
            placeholder="I know matrices but not linear transformations. Explain eigenvectors visually.",
            height=150,
        )
        duration = st.number_input("Target duration (minutes)", min_value=0.5, max_value=180.0, value=3.0, step=0.5)
        language = st.text_input("Language", value="English")
        create = st.form_submit_button("Create lesson", type="primary")
    if create:
        if not title.strip() or not request.strip():
            st.warning("Enter both a title and a lesson request.")
        else:
            try:
                created = api(
                    "POST",
                    "/projects",
                    json={
                        "title": title.strip(),
                        "request": request.strip(),
                        "target_duration_minutes": duration,
                        "language": language.strip() or "English",
                    },
                )
                st.session_state["selected_project"] = created["id"]
                st.rerun()
            except httpx.HTTPError as error:
                st.error(f"Could not create lesson: {error}")

try:
    projects = api("GET", "/projects")
except httpx.HTTPError as error:
    st.error(f"Could not list lessons: {error}")
    st.stop()

if not projects:
    st.info("Create a lesson from the sidebar to begin.")
    st.stop()

project_by_id = {project["id"]: project for project in projects}
default_id = st.session_state.get("selected_project", projects[0]["id"])
if default_id not in project_by_id:
    default_id = projects[0]["id"]
project_id = st.selectbox(
    "Lesson",
    options=list(project_by_id),
    index=list(project_by_id).index(default_id),
    format_func=lambda item: f"{project_by_id[item]['title']} · {project_by_id[item]['status']}",
)
st.session_state["selected_project"] = project_id
project = project_by_id[project_id]

left, right = st.columns([2, 1])
with left:
    st.subheader(project["title"])
    st.write(project["request"])
    st.caption(f"{project['target_duration_minutes']} minutes · {project['language']}")
with right:
    if st.button("Generate lesson", type="primary", disabled=project["status"] == "GENERATING"):
        try:
            api("POST", f"/projects/{project_id}/generate")
            st.rerun()
        except httpx.HTTPError as error:
            st.error(f"Could not start generation: {error}")

try:
    progress = api("GET", f"/projects/{project_id}/progress")
    st.subheader(f"Status: {progress['status']}")
    for event in progress["events"][-12:]:
        st.write(f"• {event['event']}")
    if progress["status"] == "GENERATING":
        st.caption("Generation is running. Refreshing automatically…")
        time.sleep(3)
        st.rerun()
except httpx.HTTPError as error:
    st.warning(f"Could not load progress: {error}")

if project.get("video_path"):
    st.subheader("Generated video")
    try:
        video = httpx.get(f"{API_URL}/projects/{project_id}/video", timeout=60)
        video.raise_for_status()
        # ISO Base Media files (including MP4) carry the `ftyp` box at bytes 4-7.
        # Do not offer fallback manifests as playable/downloadable video.
        if video.content[4:8] != b"ftyp":
            st.error(
                "This build did not produce a real MP4. Its renderer or video "
                "assembler failed; generate a new lesson after fixing the server."
            )
        else:
            st.video(video.content)
            st.download_button("Download MP4", video.content, file_name=f"{project['title']}.mp4")
    except httpx.HTTPError as error:
        st.warning(f"Could not load the video: {error}")

def parse_timestamp(value: str) -> float | None:
    """Accept 1:24, 01:24, 84, or 1:24.5 and return seconds."""
    text = value.strip().replace(";", ":")
    if not text:
        return None
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def format_timestamp(seconds: float) -> str:
    minutes, remainder = divmod(float(seconds), 60)
    return f"{int(minutes)}:{remainder:04.1f}"


timeline_segments: list[dict] = []
try:
    # /timeline returns {"video_version": n, "scenes": [{scene_id, start, end, ...}]}
    timeline_segments = (api("GET", f"/projects/{project_id}/timeline") or {}).get("scenes", [])
except httpx.HTTPError:
    pass

if project.get("video_path"):
    st.subheader("Refine a section")
    st.caption(
        "Give a timestamp range and say what should change. Only the scenes that "
        "overlap that range are regenerated — everything else is reused untouched."
    )

    if timeline_segments:
        st.write("**Scene timeline**")
        st.dataframe(
            [
                {
                    "scene": index,
                    "start": format_timestamp(segment.get("start", 0)),
                    "end": format_timestamp(segment.get("end", 0)),
                    "render": segment.get("render_version"),
                }
                for index, segment in enumerate(timeline_segments, 1)
            ],
            use_container_width=True,
            hide_index=True,
        )

    with st.form("edit-range"):
        start_col, end_col = st.columns(2)
        start_raw = start_col.text_input("From", value="1:24", help="mm:ss or seconds")
        end_raw = end_col.text_input("To", value="2:30", help="mm:ss or seconds")
        instruction = st.text_area(
            "What should change here?",
            placeholder="Explain this part more slowly, with a simpler worked example.",
            height=90,
        )
        submit_edit = st.form_submit_button("Regenerate this section", type="primary")

    if submit_edit:
        start_seconds = parse_timestamp(start_raw)
        end_seconds = parse_timestamp(end_raw)
        if start_seconds is None or end_seconds is None:
            st.error("Use mm:ss (for example 1:24) or a number of seconds.")
        elif end_seconds <= start_seconds:
            st.error("The end time must be after the start time.")
        elif not instruction.strip():
            st.error("Say what should change in this section.")
        else:
            # Half-open intersection, matching the server's range semantics.
            affected = [
                f"scene {index}"
                for index, segment in enumerate(timeline_segments, 1)
                if segment.get("start", 0) < end_seconds
                and segment.get("end", 0) > start_seconds
            ]
            try:
                response = api(
                    "POST",
                    f"/projects/{project_id}/edit-range",
                    json={
                        "start_time": start_seconds,
                        "end_time": end_seconds,
                        "instruction": instruction.strip(),
                    },
                )
                span = f"{format_timestamp(start_seconds)} – {format_timestamp(end_seconds)}"
                st.success(f"Queued a rebuild of {span}.")
                if affected:
                    st.info(f"Scenes being regenerated: {', '.join(str(a) for a in affected)}")
                st.caption(f"API response: {response}")
            except httpx.HTTPError as error:
                st.error(f"Could not queue the edit: {error}")

try:
    scenes = api("GET", f"/projects/{project_id}/scenes")
    if scenes:
        st.subheader("Scenes")
        st.dataframe(scenes, use_container_width=True, hide_index=True)
except httpx.HTTPError:
    pass
