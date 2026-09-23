# Baseline runtime; additional packages can be installed during execution.
FROM python:3.12.13-slim-bookworm
RUN pip install --no-cache-dir pytest==8.4.2 && \
    useradd --uid 10001 --create-home sandbox
USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/timeout"]
