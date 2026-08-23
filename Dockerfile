FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg build-essential pkg-config libcairo2-dev libpango1.0-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e '.[manim]'
COPY . .
RUN useradd --create-home --uid 10001 scholar && chown -R scholar:scholar /app
USER scholar

