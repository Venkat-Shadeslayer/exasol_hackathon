.PHONY: install services migrate api worker ui test demo ingest-ncert exasol exasol-verify
install:
	python -m pip install -e ".[dev]"
services:
	docker compose up -d exasol postgres redis minio
exasol:
	docker compose up -d exasol
exasol-verify:
	python scripts/verify_exasol.py
migrate:
	alembic upgrade head
api:
	uvicorn scholarmotion.api.main:app --reload
worker:
	celery -A scholarmotion.tasks.celery_app worker -l info -Q planning,retrieval,code_generation,tts,render,verification,assembly,feedback
ui:
	streamlit run frontend/app.py
test:
	pytest -q
demo:
	python scripts/create_demo.py
ingest-ncert:
	python scripts/ingest_ncert.py data/ncert

