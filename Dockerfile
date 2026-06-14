FROM debian:bookworm
LABEL by Tim Molteno "tim@elec.ac.nz"
ARG DEBIAN_FRONTEND=noninteractive

# debian setup
RUN apt-get update && apt-get install -y \
    python3-venv
RUN apt-get clean -y
RUN rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv --system-site-packages $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install tart python packages
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /object_position_server
COPY README.md .
COPY pyproject.toml .
COPY uv.lock .
RUN uv sync --no-dev --frozen

COPY tart_catalogue tart_catalogue

RUN uv sync --no-dev --frozen
RUN ls -rl
WORKDIR /object_position_server

ENV UVICORN_HOST="0.0.0.0"
ENV UVICORN_PORT="8876"
# ENV UVICORN_WORKERS="2"

CMD uvicorn tart_catalogue.main:app
