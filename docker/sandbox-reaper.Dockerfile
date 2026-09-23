FROM python:3.12.13-slim-bookworm
WORKDIR /app
COPY enterprise_agent/__init__.py enterprise_agent/__init__.py
COPY enterprise_agent/sandbox/__init__.py enterprise_agent/sandbox/__init__.py
COPY enterprise_agent/sandbox/engine.py enterprise_agent/sandbox/engine.py
COPY enterprise_agent/sandbox/reaper.py enterprise_agent/sandbox/reaper.py
COPY enterprise_agent/sandbox/storage.py enterprise_agent/sandbox/storage.py
USER 10001:10001
CMD ["python", "-m", "enterprise_agent.sandbox.reaper"]
