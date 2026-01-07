# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim as base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV OPENCV_IO_ENABLE_OPENEXR=0
ENV QT_QPA_PLATFORM=offscreen

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libpq-dev \
        tesseract-ocr \
        tesseract-ocr-ara \
        libglib2.0-0 \
        libgl1 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
        python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Create a non-privileged user
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

# Install Python dependencies with cache mount
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    python -m pip install --no-cache-dir -r requirements.txt

# Copy source code (before switching user to ensure proper permissions)
COPY --chown=appuser:appuser . .

# Create media directory with proper permissions (as root before switching user)
RUN mkdir -p /app/media/pdfs && \
    chown -R appuser:appuser /app/media && \
    chmod -R 700 /app/media

# Collect static files (run as root, then switch user)
RUN python manage.py collectstatic --noinput || true

# Switch to non-privileged user
USER appuser

# Expose port
EXPOSE 8000

# Run migrations and start server
# Use $PORT environment variable for Render compatibility
# Single worker with higher timeout to avoid OOM/timeouts during heavy PDF processing
CMD ["sh", "-c", "python manage.py migrate && gunicorn backend.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --timeout 3600"]
