FROM python:3.12.7-slim

WORKDIR /app

# Install required system dependencies (Debian-based)
RUN apt-get update && apt-get install -y \
    postgresql-client \
    gcc \
    python3-dev \
    musl-dev \
    libpq-dev \
    build-essential \
    fontconfig \
    freetype2-demos \
    zlib1g-dev \
    perl \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-xetex \
    texlive-science \
    texlive-pstricks \
    texlive-latex-recommended \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . /app/

# Set environment variables
ENV PYTHONUNBUFFERED=1
