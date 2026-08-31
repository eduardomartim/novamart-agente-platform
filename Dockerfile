# The API, packaged.
#
# Two stages, for one reason that is worth the extra lines: the builder needs a
# toolchain to turn the source tree into a wheel, and shipping that toolchain to
# production would mean shipping a compiler and a package index client to a
# process whose only job is to answer HTTP. The runtime stage installs a wheel
# and a hashed dependency set, and nothing else.
#
# The package is *installed*, not copied. That is deliberate: copying the
# checkout in would hide any packaging mistake -- a data file missing from the
# wheel, a module outside the package -- until some later environment that had
# no checkout to fall back on. Installing it here means the image proves the
# package is genuinely installable every time it is built.

# ---------------------------------------------------------------- builder ---
FROM python:3.12-slim-trixie AS builder

WORKDIR /build

# Build the wheel from the source tree. `hatchling` comes from pyproject's
# build-system table, fetched by pip's build isolation.
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN python -m pip install --no-cache-dir build==1.3.0 \
 && python -m build --wheel --outdir /build/dist

# ---------------------------------------------------------------- runtime ---
FROM python:3.12-slim-trixie AS runtime

# Fixed uid/gid rather than a name. Kubernetes `runAsUser` takes a number, and
# a numeric owner here means the same identity holds whether the image is run
# by docker, by compose, or by a pod spec that never reads /etc/passwd.
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} app \
 && useradd --uid ${APP_UID} --gid ${APP_GID} --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Patch the base image's OpenSSL before anything else is installed.
#
# `docker scout` reported 2 CRITICAL and 5 HIGH against this base tag, every one
# of them a Debian package rather than a Python one: openssl (fixed in
# 3.5.7-1~deb13u2) and perl-base (no fix published). This step takes it to
# 2 CRITICAL and 2 HIGH by upgrading only the packages that have a patch --
# a blanket `apt-get upgrade` would pull unrelated versions in and make the
# image differ for reasons nobody chose.
#
# perl-base is deliberately left alone. Debian publishes no fixed version, the
# base tag is already current per `docker scout recommendations`, and the package
# is `Essential: yes` so dpkg refuses to remove it. Forcing it out to lower a
# scanner number would trade real fragility for a cosmetic win, so the remaining
# four findings are recorded as accepted rather than papered over.
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade \
      openssl libssl3t64 openssl-provider-legacy \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# --no-compile because PYTHONDONTWRITEBYTECODE governs *run* time while pip
# compiles during *install*, leaving __pycache__ trees that add weight and
# that the app user could never regenerate -- the install tree is read-only
# to it.
#
# Dependencies first, from the hashed lock, so this layer is cached until the
# lock actually changes. `--require-hashes` is implied by the file's contents:
# every pin carries its hashes, and pip refuses the install if any artefact does
# not match.
COPY requirements-api.txt /tmp/requirements-api.txt
RUN python -m pip install --no-cache-dir --no-compile --require-hashes -r /tmp/requirements-api.txt \
 && rm /tmp/requirements-api.txt

# Then the application wheel. `--no-deps` because the lock above is the single
# source of truth for what gets installed; letting pip resolve again here could
# pull an unpinned version and quietly undo the lock.
COPY --from=builder /build/dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir --no-compile --no-deps /tmp/*.whl \
 && rm -rf /tmp/*.whl \
 && python -m pip uninstall -y pip setuptools wheel 2>/dev/null || true \
 && find /usr/local/lib/python3.12 -depth -name '__pycache__' -type d -exec rm -rf {} + \
 && find /usr/local/lib/python3.12 -name '*.pyc' -delete

# The persisted vector index, at an explicit path named by an environment
# variable. Demo traffic never reads it -- stub mode is BM25 over FTS5 and is
# fully offline -- but hybrid retrieval needs it, and an image that silently
# lacked it would fail only once a real provider was configured.
#
# The directory is created explicitly, before the COPY. BuildKit applies
# `--chmod` to parent directories it has to create as well, so letting COPY
# create this one gave it mode 0444 -- readable but not traversable, which made
# the index unreachable to every user including the one that needs it. The file
# is data: read-only to all, executable by none.
RUN install -d -o root -g root -m 0755 /opt/agent-platform
COPY --chown=root:root --chmod=0444 data/kb_vectors.db /opt/agent-platform/kb_vectors.db

# The one directory the process writes to. Everything else can be read-only,
# which is what `read_only: true` in compose relies on. SQLite is given an
# absolute path so it can never land somewhere unexpected because the working
# directory moved.
RUN install -d -o ${APP_UID} -g ${APP_GID} -m 0755 /var/lib/agent-platform

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    DATABASE_PATH=/var/lib/agent-platform/agent_platform.db \
    KB_VECTOR_INDEX_PATH=/opt/agent-platform/kb_vectors.db

# No GEMINI_API_KEY. Its absence is what selects the deterministic stub, so the
# image runs offline by default and a key is something an operator supplies at
# run time -- never something baked into a layer.

EXPOSE 8000

USER ${APP_UID}:${APP_GID}

# Liveness only: is this process answering? It opens no database, calls no
# provider, and spends no budget. Readiness is a separate endpoint because a
# liveness probe that checks dependencies restarts every replica at once the
# moment a dependency blips.
#
# Shell form on purpose, so ${API_PORT} is expanded at run time: a healthcheck
# pinned to 8000 would report a container unhealthy the moment someone changed
# the port, and a false unhealthy is worse than no healthcheck.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import os,sys,urllib.request; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('API_PORT','8000')+'/health',timeout=2).status==200 else 1)"

# Exec form, so the interpreter is PID 1 and receives SIGTERM directly. uvicorn
# handles it: it stops accepting connections and lets in-flight requests finish.
ENTRYPOINT ["python", "-m", "agent_platform.api"]
