FROM python:3.12.7-alpine

WORKDIR /app

# Gerekli sistem bağımlılıklarını yükleyin
RUN apk update && apk add --no-cache \
    postgresql-dev \
    gcc \
    python3-dev \
    musl-dev \
    fontconfig-dev \
    freetype-dev \
    zlib-dev \
    perl

# Minimal texlive kurulumu ve gerekli LaTeX paketlerini yükleyin
RUN apk add --no-cache texlive \
    texmf-dist-latexextra \
    texmf-dist-fontsextra \
    texmf-dist-mathscience \
    texmf-dist-pictures \
    texmf-dist-pstricks \
    texmf-dist-latexrecommended

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

ENV PYTHONUNBUFFERED=1

