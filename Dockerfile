# syntax=docker/dockerfile:1.6
#
# The inference engine is Tinfoil's production gemma4-31b image, pinned by digest
# (confidential-gemma4-31b v0.0.25, vLLM 0.25.1 with the CC patch set). This image
# only adds the eval harness beside it; nothing about the engine changes.
ARG BASE_IMAGE=ghcr.io/tinfoilsh/confidential-gemma4-31b@sha256:cb45fc53829f73b588c26fa9ca6c90be122367a64e3b835ce4571a4e5f839d89
FROM ${BASE_IMAGE}

ARG SOURCE_REVISION=unversioned
ARG VERSION=unversioned

# uv, pinned by digest, installs the one extra dependency the harness needs.
COPY --from=ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 /uv /bin/uv
RUN set -eux; \
    uv pip install --system --no-cache "cryptography>=42,<47"; \
    python3 -c "import fastapi, uvicorn, httpx, yaml, cryptography; print('harness deps ok', fastapi.__version__, uvicorn.__version__, httpx.__version__, cryptography.__version__)"

COPY dbe/ /opt/dbe/dbe/
COPY harness/ /opt/dbe/harness/
COPY entrypoint.sh /opt/dbe/entrypoint.sh
RUN set -eux; \
    chmod 0755 /opt/dbe/entrypoint.sh; \
    find /opt/dbe -name '__pycache__' -type d -exec rm -rf {} + || true; \
    cd /opt/dbe && python3 -c "import harness.app, dbe.canonical; print('harness import ok')"; \
    test -f /vllm-workspace/examples/tool_chat_template_gemma4.jinja

ENV PYTHONPATH=/opt/dbe \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/tinfoilsh/double-blind-eval" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${VERSION}" \
      com.tinfoil.base-image="confidential-gemma4-31b@sha256:cb45fc53829f73b588c26fa9ca6c90be122367a64e3b835ce4571a4e5f839d89"

# Same working directory as the production image: vLLM's relative paths
# (chat template) resolve from here.
WORKDIR /vllm-workspace
ENTRYPOINT ["/opt/dbe/entrypoint.sh"]
