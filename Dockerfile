# Dockerfile for Dota 2 Socket Scanner - Local Development
# Usage: docker build -t dota2-scanner . && docker run --env-file .env dota2-scanner

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (for asyncpg, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY alembic.ini .
COPY pyproject.toml .
COPY README.md .

# Install package in development mode
RUN pip install --no-cache-dir -e .

# Create data directory for gems.json
RUN mkdir -p /app/data

# Default command runs the scanner
CMD ["python", "-m", "src.main"]

# Alternative commands:
# docker run --env-file .env dota2-scanner alembic upgrade head
# docker run --env-file .env dota2-scanner python -m pytest
# docker run --env-file .env dota2-scanner ruff check .
# docker run --env-file .env dota2-scanner mypy src