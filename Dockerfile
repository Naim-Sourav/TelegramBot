FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Dhaka

WORKDIR /app

# curl is handy for health checks in compose
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

RUN useradd -m -u 10001 autopost \
    && mkdir -p /app/data \
    && chown -R autopost:autopost /app
USER autopost

VOLUME ["/app/data"]

# `autopost run` = scheduler + review bot in one process
CMD ["autopost", "run"]
