FROM debian:bookworm
MAINTAINER Tim Molteno "tim@elec.ac.nz"
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
RUN pip install --no-cache-dir --no-compile poetry

WORKDIR /object_position_server
COPY README.md .
COPY pyproject.toml .
RUN poetry install --without=dev --no-root

COPY tart_catalogue tart_catalogue

RUN poetry install --without=dev
RUN ls -rl
WORKDIR /object_position_server

ENV UVICORN_HOST="0.0.0.0"
ENV UVICORN_PORT="8876"
# ENV UVICORN_WORKERS="2"

CMD uvicorn tart_catalogue.main:app
