FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        jq \
        curl ca-certificates \
        git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Pre-create a non-root user (use uid 1001 — ubuntu:24.04 already has uid 1000).
RUN useradd -m -s /bin/bash -u 1001 sandbox

# Pre-create a python venv as sandbox user (reusable across runs).
USER sandbox
WORKDIR /home/sandbox
RUN python3 -m venv venv

# Default working dir when commands are exec'd.
WORKDIR /workspace
