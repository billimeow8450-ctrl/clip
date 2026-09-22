# Production Dockerfile for Clip Studio AI Backend
FROM python:3.11-slim

# Install system dependencies including FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY backend/requirements.txt /app/backend/requirements.txt
COPY WEBSITE_DEVELOPER_SOURCE/requirements.txt /app/engine-requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt -r /app/engine-requirements.txt

# Copy application files
COPY backend /app/backend
COPY WEBSITE_DEVELOPER_SOURCE /app/WEBSITE_DEVELOPER_SOURCE

# Storage directories
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/backend/storage/uploads /app/backend/storage/outputs /app/backend/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENV ENVIRONMENT=production
ENV ENABLE_HEAVY_RENDERING=1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
