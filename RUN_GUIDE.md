# ScholarMotion — Run Guide

Two supported paths. Pick one:

| Path | Use when | Needs | Real video output? |
|---|---|---|---|
| **A — Docker Compose** | You want the full stack exactly as deployed | Docker + Compose | ✅ Yes (Manim + FFmpeg in image) |
| **B — Local Python** | You want to read/debug code, or have no Docker | Python 3.11–3.12 | ⚠️ Only if you install Manim + FFmpeg |

Neither path requires an API key. Default providers are deterministic mocks.

---

## Path A — Docker Compose (recommended)

### 1. Configure

```bash
cd Manim-Generator
cp .env.example .env
```

The defaults in `.env.example` already point at the Compose service names and use `mock` LLM/TTS providers. **Edit nothing** for a first run.

### 2. Bring the stack up

```bash
docker compose up -d --build
```

This starts seven services:

| Service | Image / build | Port |
|---|---|---|
| `exasol` | `exasol/docker-db:2025.1.14` — **corpus data platform** | 8563, 2580 |
| `postgres` | `pgvector/pgvector:pg16` | 5432 |
| `redis` | `redis:7-alpine` | 6379 |
| `minio` | `minio/minio:latest` | 9000, 9001 |
| `api` | local `Dockerfile` | 8000 |
| `worker` | local `Dockerfile` | — |
| `frontend` | local `Dockerfile` | 8501 |

The first build compiles the Manim toolchain and takes several minutes.

> **Exasol needs a long first start.** The `exasol/docker-db` image is multi-gigabyte, runs `privileged`, and initialises its own storage volume on first boot — allow **5–10 minutes** before it reports healthy. `api` and `worker` are gated on that healthcheck, so they will sit in `Created` until it passes. Watch it with `docker compose logs -f exasol`.
>
> Give Docker Desktop at least **6 GB of memory** (Settings → Resources), or Exasol will fail to initialise.

### 2a. Verify Exasol

Once the stack is up:

```bash
curl http://localhost:8000/corpus/health
```

Expect `{"enabled": true, "reachable": true, "scoring": "udf", ...}`. If `scoring` is `"sql"`, the Python UDF could not be deployed on your instance and retrieval is using the columnar SQL dot-product fallback — correct results, just a different execution path.

For a full end-to-end proof (connect → bootstrap → bulk load → query on *both* scoring paths → analytics):

```bash
docker compose exec api python scripts/verify_exasol.py
```

### 3. Migrate the database

Wait for `postgres` to report healthy, then:

```bash
docker compose exec api alembic upgrade head
```

### 4. Verify

```bash
curl http://localhost:8000/health
docker compose ps          # all services Up; postgres/redis healthy
docker compose logs -f worker
```

### 5. Open

- **Streamlit UI** — <http://localhost:8501>
- **API docs (OpenAPI)** — <http://localhost:8000/docs>
- **MinIO console** — <http://localhost:9001> — user `minio`, password `miniosecret`

### 6. Shut down

```bash
docker compose down          # keep volumes
docker compose down -v       # also delete exasol/postgres/redis/minio data
```

---

## Path B — Local Python (no Docker)

### 1. Python version

Requires **Python 3.11 or newer**. Note that some pinned dependencies (notably `sentence-transformers` and `manim`) are not reliably installable on Python 3.13 — if you are on 3.13, prefer 3.12:

```bash
python3 --version            # confirm before proceeding
```

### 2. Virtual environment and install

```bash
cd Manim-Generator
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Install the Exasol driver — `pyexasol` is pure Python over WebSocket, so there is no ODBC or compiler to set up:

```bash
python -m pip install -e ".[exasol]"
```

Other optional extras:

```bash
python -m pip install -e ".[llm]"          # anthropic, google-genai, openai
python -m pip install -e ".[embeddings]"   # sentence-transformers
python -m pip install -e ".[manim]"        # manim
python -m pip install -e ".[s3]"           # boto3
```

### 3. Configure for keyless SQLite mode

```bash
cp .env.example .env
```

Then override the two settings that assume Docker. Either edit `.env`, or export in your shell:

```bash
export DATABASE_URL="sqlite+aiosqlite:///./scholarmotion.db"
export CELERY_TASK_ALWAYS_EAGER=true
```

Windows `cmd`:

```bat
set DATABASE_URL=sqlite+aiosqlite:///./scholarmotion.db
set CELERY_TASK_ALWAYS_EAGER=true
```

`CELERY_TASK_ALWAYS_EAGER=true` runs tasks inline in-process, so **you do not need Redis or a Celery worker** on this path.

### 4. Migrate

```bash
alembic upgrade head
```

### 5. Run

Two terminals, both with the venv activated and the same env vars exported.

```bash
# terminal 1 — API
uvicorn scholarmotion.api.main:app --reload

# terminal 2 — UI
streamlit run frontend/app.py
```

Open <http://localhost:8501> and <http://localhost:8000/docs>.

### 6. Exasol on the local path

Retrieval runs on Exasol, so start it even when the rest of the app is local Python.

**On macOS (including Apple Silicon) use Exasol Personal — it is a native arm64 binary and starts in seconds:**

```bash
curl https://www.exasol.com/install/ | sh   # installs ~/.local/bin/exasol
export PATH="$HOME/.local/bin:$PATH"
exasol install local
exasol info                                  # deployment state, connection details
```

It listens on `127.0.0.1:8563`, user `sys`. The password is generated into
`~/.exasol/personal/deployments/default/secrets.json` — copy it into `EXASOL_PASSWORD`:

```bash
python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.exasol/personal/deployments/default/secrets.json')))['dbPassword'])"
```

Exasol Personal serves a self-signed certificate, so leave `EXASOL_VERIFY_TLS=false` (the default).

On an **x86_64 Linux** host you can use the Compose service instead:

```bash
docker compose up -d exasol          # first boot takes 5-10 minutes
docker compose logs -f exasol        # wait for it to finish initialising
```

Then confirm and bootstrap the schema:

```bash
python scripts/verify_exasol.py
```

Keep `EXASOL_DSN=localhost:8563` in `.env` for this path (the Compose `api`/`worker` services override it to `exasol:8563` internally).

If you cannot run Exasol at all, set `EXASOL_ENABLED=false`. Generation still works — it degrades to the PostgreSQL or in-process retriever — but you are no longer exercising the corpus platform.

### 7. Optional: real queues locally

If you want actual Celery queues instead of eager mode, start the backing services with Docker and set `CELERY_TASK_ALWAYS_EAGER=false`:

```bash
make services      # exasol + postgres + redis + minio
make migrate
make worker        # in its own terminal
```

---

## Media toolchain (Manim + FFmpeg)

Path A includes both in the image. On Path B you must install them yourself for real video.

```bash
# macOS
brew install ffmpeg
brew install --cask mactex-no-gui      # LaTeX, needed for Manim's Tex/MathTex
python -m pip install -e ".[manim]"

# Debian/Ubuntu
sudo apt-get install -y ffmpeg libcairo2-dev libpango1.0-dev texlive-full
python -m pip install -e ".[manim]"
```

Verify:

```bash
ffmpeg -version | head -1
manim --version
```

**Without them the app still runs.** Mock mode writes deterministic placeholder media so orchestration, editing, verification, and the full artifact/version model remain exercisable — only real playback requires the toolchain. If Manim or FFmpeg is on a non-standard path, point at it with `MANIM_BINARY` / `FFMPEG_BINARY`.

---

## Full local stack — the four services

This is the configuration the demo runs on. Each check tells you whether to start anything.

```bash
cd Manim-Generator
export PATH="$HOME/.local/bin:$PATH"

exasol info 2>/dev/null | grep -i "^Deployment State"          # 1. corpus platform
curl -s http://127.0.0.1:8811/health; echo                     # 2. speech
curl -s http://127.0.0.1:8000/corpus/health; echo              # 3. API + Exasol link
curl -s -o /dev/null -w "streamlit %{http_code}\n" http://127.0.0.1:8501   # 4. UI
```

All healthy looks like:

```
Deployment State: running
{"status":"ok","device":"cpu","cuda_devices":0}
{"enabled":true,"reachable":true,"schema":"SCHOLARMOTION","scoring":"sql"}
streamlit 200
```

Start only what is missing:

| Service | Command |
|---|---|
| Exasol Personal | `exasol install local` |
| Kokoro TTS | `conda activate kokoro-tts && uvicorn scripts.kokoro_server:app --host 127.0.0.1 --port 8811` |
| API | `MAX_CONCURRENT_SCENES=1 MAX_LLM_REQUESTS=1 CELERY_TASK_ALWAYS_EAGER=true uvicorn scholarmotion.api.main:app --host 127.0.0.1 --port 8000` |
| Streamlit UI | `streamlit run frontend/app.py` |

> `[Errno 48] address already in use` means the service is **already running** — not a failure.
> To force a restart: `lsof -ti:<port> | xargs kill`, then run the command again.

Then open <http://localhost:8501>.

**Using the UI:** *New lesson* in the sidebar → describe what you don't understand → *Create
lesson* → *Generate lesson*. When it completes, **Refine a section** takes a timestamp range
(`1:24` to `2:30`) plus an instruction and regenerates only the scenes overlapping that window;
the scene table's render-version column shows which scenes changed and which were reused.

`MAX_CONCURRENT_SCENES=1 MAX_LLM_REQUESTS=1` keeps you inside Gemini's free-tier limit of 15
requests per minute. Raise them if you have a paid key.

## First end-to-end run

### Fastest: a scripted demo

```bash
python scripts/create_demo.py
```

Builds an NCERT-level electromagnetic-induction lesson with deterministic providers, then simulates a localized content edit and an overlap defect. Other demos: `create_emi_demo.py`, `create_lenz_law_demo.py`, `create_kirchhoff_demo.py`.

### Or drive the API directly

```bash
# 1. Create a project — note the returned id
curl -s -X POST http://localhost:8000/projects \
  -H "content-type: application/json" \
  -d '{
        "title": "Eigenvectors",
        "request": "I understand matrices but not linear transformations. Explain eigenvectors visually.",
        "target_duration_minutes": 5,
        "language": "English"
      }'

# 2. Generate
export PID=<project id from step 1>
curl -X POST http://localhost:8000/projects/$PID/generate

# 3. Poll
curl http://localhost:8000/projects/$PID/progress
curl http://localhost:8000/projects/$PID/scenes

# 4. Fetch results
curl http://localhost:8000/projects/$PID/video
curl http://localhost:8000/projects/$PID/timeline
```

### Then edit a range

```bash
curl -X POST http://localhost:8000/projects/$PID/edit-range \
  -H "content-type: application/json" \
  -d '{"start_time":152.1,"end_time":160.75,"instruction":"Use a simpler example here."}'
```

Only the scenes intersecting `[152.1, 160.75)` regenerate. Confirm with `/scenes/{scene_id}/versions` — untouched scenes keep their existing version.

Artifacts land under `projects/<project-id>/`:

```
projects/<project-id>/
  artifacts/{profile,retrieval,curriculum,pedagogy,script,storyboard}/vN.json
  scenes/S01/{spec,code,audio,render}/vN.*
```

---

## Adding real providers

Edit `.env`, then restart the API and worker.

```bash
# Anthropic
MAIN_LLM_PROVIDER=anthropic
MAIN_LLM_API_KEY=sk-ant-...
MAIN_LLM_MODEL=claude-opus-4-5

# OpenAI-compatible
MAIN_LLM_PROVIDER=openai
MAIN_LLM_API_KEY=sk-...
MAIN_LLM_MODEL=gpt-4o
MAIN_LLM_BASE_URL=            # set for a self-hosted gateway

# Gemini
MAIN_LLM_PROVIDER=gemini
MAIN_LLM_API_KEY=...
```

TTS options:

```bash
# ElevenLabs — TTS_API_KEY is your xi-api-key, TTS_VOICE is a voice_id
TTS_PROVIDER=elevenlabs
TTS_API_KEY=...
TTS_MODEL=eleven_flash_v2_5
TTS_VOICE=Rachel

# Local Kokoro-82M server
python scripts/kokoro_server.py     # separate terminal
TTS_PROVIDER=kokoro_http
TTS_BASE_URL=http://127.0.0.1:8811
TTS_VOICE=af_heart
```

Only `openai` (not `openai_compatible`) is registered as image-capable. If your main provider cannot inspect images, set `VISUAL_LLM_PROVIDER` to enable multimodal frame verification; otherwise that one layer is skipped and the deterministic layers still run.

Keep credentials in `.env.local` rather than `.env` if you want them isolated — it is gitignored and loaded after `.env`, so it wins.

---

## Ingesting sources

```bash
# NCERT PDFs — place under data/ncert/, ideally with class/subject in the path
python scripts/ingest_ncert.py data/ncert/
# or
make ingest-ncert
```

Research papers: upload from the Streamlit sidebar, or `POST /projects/{id}/sources`.

---

## Correction memory maintenance

```bash
python scripts/run_correction_bootstrap.py    # seed validated failure patterns
python scripts/rebuild_correction_index.py    # rebuild the lookup index
```

---

## Make targets

| Target | Runs |
|---|---|
| `make install` | `pip install -e ".[dev]"` |
| `make services` | `docker compose up -d exasol postgres redis minio` |
| `make exasol` | `docker compose up -d exasol` |
| `make exasol-verify` | `python scripts/verify_exasol.py` |
| `make migrate` | `alembic upgrade head` |
| `make api` | `uvicorn scholarmotion.api.main:app --reload` |
| `make worker` | Celery worker on all named queues |
| `make ui` | `streamlit run frontend/app.py` |
| `make test` | `pytest -q` — see caveat below |
| `make demo` | `python scripts/create_demo.py` |
| `make ingest-ncert` | `python scripts/ingest_ncert.py data/ncert` |

---

## Troubleshooting

**`pytest` only runs a handful of tests.**
Expected. The only checked-in suite is `tests/test_exasol_retrieval.py` (Exasol scoring and SQL generation, no live server needed). The rest of the pipeline has no automated coverage yet — use `scripts/create_demo.py` and `scripts/verify_exasol.py` as end-to-end smoke checks.

**`cannot reach Exasol at ...` during generation or ingest.**
Exasol is not up yet — first boot takes 5–10 minutes. Check `docker compose ps exasol` and `docker compose logs exasol`. Generation does not hard-fail on this: it logs a warning and falls back to the PostgreSQL/in-process retriever, so a build that "works" may not have used Exasol. Confirm with `curl http://localhost:8000/corpus/health`.

**`pyexasol is not installed`.**
Run `pip install -e ".[exasol]"`.

**`/corpus/health` reports `"scoring": "sql"` instead of `"udf"`.**
The `COSINE_SIMILARITY` Python UDF could not be created — usually the connecting user lacks script-creation rights. Results are still correct via the columnar dot-product fallback. To force the SQL path deliberately, set `EXASOL_USE_UDF=false`.

**`/corpus/analytics` returns 503.**
Either `EXASOL_ENABLED=false`, or Exasol is unreachable. The response body says which.

**Apple Silicon: use Exasol Personal, not the Docker image.**
`exasol/docker-db` is x86_64-only and **does not run on Apple Silicon.** Under emulation it stalls permanently: `exainit` finishes stage1, logs `Next stage will be 'stage2'`, and stage2 never executes. The container sits at **0% CPU** with only `cos_cored`/`super_cored` running — no database. Port 8563 still accepts TCP (that is the cluster daemon), so clients fail later with `SSL: UNEXPECTED_EOF_WHILE_READING`, which is misleading — nothing is listening for SQL.

Use the native arm64 **Exasol Personal** launcher instead (see *Exasol on the local path* above). The Compose `exasol` service is only for x86_64 hosts.

**Exasol Personal: `No usable script language container is installed`.**
Local Exasol Personal ships without the Python3 script language container, so the `COSINE_SIMILARITY` UDF cannot be created. This is expected and harmless — retrieval automatically uses the columnar SQL dot-product path. `GET /corpus/health` reports `"scoring": "sql"`.

**`Feature not supported: host parameter specification`.**
A query reached Exasol with unsubstituted placeholders. pyexasol interpolates `{name}`, **not** SQLAlchemy-style `:name`; numeric literals need the `!d` spec (`LIMIT {limit!d}`), or they are quoted as strings and Exasol rejects them.

**`syntax error, unexpected SECTION_`.**
`SECTION`, `TEXT` and `VALUE` are reserved words in Exasol (`SYS.EXA_SQL_KEYWORDS`) and must be double-quoted wherever they appear.

**Exasol container exits or never becomes healthy — x86_64 hosts.**
It needs `privileged: true` (already set) and real memory. Raise Docker Desktop's memory limit to 6 GB+ under Settings → Resources.

**Retrieval returns nothing after ingesting.**
The corpus is loaded into Exasol by `scripts/ingest_ncert.py` only when `EXASOL_ENABLED=true` at ingest time. If you ingested with it off, re-run the ingest with it on — loads are idempotent, chunks are replaced by id.

**`alembic upgrade head` fails with a connection error.**
`DATABASE_URL` is pointing at a PostgreSQL host that is not up. Either start it (`make services`) and wait for the healthcheck, or switch to `sqlite+aiosqlite:///./scholarmotion.db`.

**Generation submits but nothing progresses.**
Tasks are queued with no consumer. Either start a worker (`make worker`) or set `CELERY_TASK_ALWAYS_EAGER=true` and restart the API.

**`manim: command not found` / renders produce placeholder media.**
Manim and/or FFmpeg are missing on Path B — see [Media toolchain](#media-toolchain-manim--ffmpeg). Mock artifacts are the designed fallback, so the pipeline will not error.

**Manim fails on `Tex` / `MathTex`.**
Missing LaTeX. Install a TeX distribution (`mactex-no-gui` on macOS, `texlive-full` on Debian/Ubuntu).

**pgvector errors on SQLite.**
Vector columns are only used on PostgreSQL; SQLite falls back to a JSON representation and a different retriever. If you see pgvector SQL against SQLite, confirm `DATABASE_URL` really is the SQLite URL in the process that is running — check for a stale `.env.local` overriding it.

**`sentence-transformers` will not install.**
You are likely on Python 3.13. Recreate the venv on 3.12, or set `EMBEDDING_PROVIDER=openai` with an API key, or stay in mock mode.

**Port already in use (8000 / 8501 / 5432).**
Stop the conflicting process, or remap in `docker-compose.yml` / pass `--port` to `uvicorn` and `--server.port` to `streamlit`.

**Config changes are ignored.**
Settings are cached with `@lru_cache` for the process lifetime, and `.env.local` is loaded *after* `.env`. Restart the process, and check whether a `.env.local` is shadowing your edit.
