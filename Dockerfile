FROM debian:bookworm
LABEL by Tim Molteno "tim@elec.ac.nz"
ARG DEBIAN_FRONTEND=noninteractive

# debian setup - python3 is already in bookworm, no extra packages needed
RUN apt-get update && apt-get clean -y && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

# Copy uv binary from the official uv image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /object_position_server
COPY README.md .
COPY pyproject.toml .
COPY uv.lock .
RUN uv sync --no-dev --frozen

COPY tart_catalogue tart_catalogue

RUN uv sync --no-dev --frozen

ENV UVICORN_HOST="0.0.0.0"
ENV UVICORN_PORT="8876"

CMD uv run uvicorn tart_catalogue.main:app
