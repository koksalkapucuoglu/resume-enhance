FROM python:3.12.7-alpine

WORKDIR /app

RUN apk update && apk add --no-cache postgresql-dev gcc python3-dev musl-dev


COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

ENV PYTHONUNBUFFERED=1

