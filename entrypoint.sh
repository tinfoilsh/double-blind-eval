#!/bin/bash
# Runs vLLM (loopback only) and the eval harness (shim-facing) in one container.
# vLLM's arguments come from `command:` in tinfoil-config.yml so they stay measured and reviewable.
set -euo pipefail

: "${DBE_HARNESS_PORT:=8080}"
: "${DBE_STATE_DIR:=/run/dbe}"
mkdir -p "${DBE_STATE_DIR}"

# vLLM resolves relative paths such as the gemma4 chat template against the
# base image's working directory, so run it from there exactly as production does.
VLLM_WORKDIR="${VLLM_WORKDIR:-/vllm-workspace}"
echo "dbe: starting vLLM from ${VLLM_WORKDIR}"
(cd "${VLLM_WORKDIR}" && exec python3 -m vllm.entrypoints.openai.api_server "$@") &
VLLM_PID=$!

echo "dbe: starting harness on :${DBE_HARNESS_PORT}"
(cd /opt/dbe && exec python3 -m uvicorn harness.app:create_app_from_env --factory \
  --host 0.0.0.0 --port "${DBE_HARNESS_PORT}" --log-level info --no-access-log) &
HARNESS_PID=$!

stop() { kill -TERM "$VLLM_PID" "$HARNESS_PID" 2>/dev/null || true; }
trap stop TERM INT

wait -n "$VLLM_PID" "$HARNESS_PID" || true
echo "dbe: a process exited; stopping the other"
stop
wait || true
exit 1
