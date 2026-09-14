# CodeLens backend — the parser, the graph, and the API.
#
# Two stages: wheels are built where a compiler exists, and the runtime image
# gets only the installed packages. tree-sitter grammars are C extensions, so
# without the split every deployed image would carry gcc and the Python
# headers for the life of the container.
#
# `git` is a genuine *runtime* dependency here, not a build one, and it is the
# easiest thing to leave out by accident: ingestion shells out to `git clone`
# and `git log`. An image without it starts cleanly, serves /health, and fails
# on the first analysis with an error that reads like a network problem.

FROM python:3.13-slim AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Unbuffered so container logs appear as they happen rather than when a
# buffer fills — the difference between watching a clone and watching nothing.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

WORKDIR /app
COPY app ./app
COPY fixtures ./fixtures
COPY scripts ./scripts
COPY pyproject.toml ./

# Both are written at runtime and both must survive a restart to be worth
# anything: the database is the analysed graphs, the clone dir is the cache
# that makes re-analysis free. Declared as volumes in compose.
RUN mkdir -p /data /var/cache/codelens
ENV SQLITE_PATH=/data/codelens.db \
    CLONE_DIR=/var/cache/codelens

# Non-root, and it owns the two writable paths. A container that runs as root
# to write its own data volume is one path-traversal bug from a bad day.
RUN useradd --system --uid 10001 codelens \
    && chown -R codelens:codelens /data /var/cache/codelens /app
USER codelens

EXPOSE 8000

# The same endpoint the frontend and run.sh already poll, so "healthy" means
# one thing everywhere.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# One worker on purpose. The job registry, the rate limiter, the concurrency
# gate and the graph cache are all per *process* — a second worker would
# silently double every limit and answer job polls from a process that has
# never heard of the job. Scaling past one worker is the shared-store work
# core/limits.py names, not a flag change here.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
