# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
# build-essential & gcc for compiling C extensions (if any fallback from wheels)
# libpq-dev for building psycopg2 (if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install dependencies to a temporary location
# We use --user install approach or --prefix to easily copy
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Final
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
# - WeasyPrint needs: libpango, libpangocairo, libgdk-pixbuf, shared-mime-info, libcairo2
# - Postgres: postgresql-client (for management/debugging)
# - Fonts: fonts-liberation, fontconfig (crucial for PDF rendering)
# - No LaTeX packages installed as per MVP requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    fonts-liberation \
    fontconfig \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed python dependencies from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Create a non-root user for security (Best Practice)
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8000

# Default command (Use gunicorn in production)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "core.wsgi:application"]
