# Python Alpine sandbox image for Jean-Michel.
# Aim: smallest possible image with Python 3 + common data tools.
# ~120 MB vs ~350 MB for ubuntu:24.04.
#
# Build: ./jm.sh --build-docker             (default tag: jeanmichel-sandbox:py-alpine)
# Build: ./jm.sh --build-docker py-alpine   (explicit)
FROM python:3.13-alpine

# Runtime packages — curl & git are useful but absent from network-isolated
# containers; they're here for cases where the operator relaxes --network=none.
RUN apk add --no-cache \
        bash \
        jq \
        curl \
        ca-certificates \
        git \
    && rm -rf /var/cache/apk/*

# Non-root sandbox user.
RUN addgroup -S sandbox && adduser -S -G sandbox -u 1000 sandbox

# Pre-create a Python venv owned by the sandbox user.
USER sandbox
WORKDIR /home/sandbox
RUN python3 -m venv venv

# Install commonly useful Python packages into the venv.
RUN venv/bin/pip install --no-cache-dir \
        requests \
        numpy \
        tabulate

# Default working directory when commands are exec'd via docker exec.
WORKDIR /workspace
