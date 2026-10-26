#!/usr/bin/env bash

set -Eeuo pipefail

API_VERSION="2026-03-10"

if [[ -z "${GH_REPO:-}" ]]; then
    echo "ERROR: GH_REPO is required."
    echo "Example: GH_REPO=dprime999/HILTester"
    exit 1
fi

if [[ -z "${GH_TOKEN:-}" ]]; then
    echo "ERROR: GH_TOKEN is required."
    exit 1
fi

if [[ "${GH_REPO}" != */* ]]; then
    echo "ERROR: GH_REPO must use owner/repository format."
    echo "Example: dprime999/HILTester"
    exit 1
fi

OWNER="${GH_REPO%%/*}"
REPO="${GH_REPO#*/}"

RUNNER_NAME="${RUNNER_NAME:-hil-runner-$(hostname)}"
RUNNER_LABELS="${RUNNER_LABELS:-hil,docker}"

RUNNER_PID=""

github_api_post() {
    local endpoint="$1"

    curl \
        --fail \
        --silent \
        --show-error \
        --location \
        --request POST \
        --header "Accept: application/vnd.github+json" \
        --header "Authorization: Bearer ${GH_TOKEN}" \
        --header "X-GitHub-Api-Version: ${API_VERSION}" \
        "${endpoint}"
}

get_registration_token() {
    github_api_post \
        "https://api.github.com/repos/${OWNER}/${REPO}/actions/runners/registration-token" \
        | jq -r '.token'
}

get_removal_token() {
    github_api_post \
        "https://api.github.com/repos/${OWNER}/${REPO}/actions/runners/remove-token" \
        | jq -r '.token'
}

unregister_runner() {
    echo
    echo "Attempting to unregister GitHub Actions runner..."

    local remove_token

    if ! remove_token="$(get_removal_token)"; then
        echo "WARNING: Could not obtain runner removal token."
        return
    fi

    if [[ -z "${remove_token}" || "${remove_token}" == "null" ]]; then
        echo "WARNING: GitHub returned an invalid removal token."
        return
    fi

    if ./config.sh remove --token "${remove_token}"; then
        echo "Runner successfully removed from GitHub."
    else
        echo "WARNING: Runner could not be automatically removed."
    fi
}

shutdown() {
    echo
    echo "Shutdown requested..."

    trap - SIGINT SIGTERM

    if [[ -n "${RUNNER_PID}" ]] && kill -0 "${RUNNER_PID}" 2>/dev/null; then
        echo "Stopping GitHub Actions runner..."
        kill -TERM "${RUNNER_PID}" 2>/dev/null || true
        wait "${RUNNER_PID}" 2>/dev/null || true
    fi

    unregister_runner

    exit 0
}

trap shutdown SIGINT SIGTERM

echo "=================================================="
echo "HIL GitHub Actions Runner"
echo "=================================================="
echo "Repository : ${GH_REPO}"
echo "Runner     : ${RUNNER_NAME}"
echo "Labels     : ${RUNNER_LABELS}"
echo "=================================================="

echo
echo "Requesting temporary runner registration token..."

REG_TOKEN="$(get_registration_token)"

if [[ -z "${REG_TOKEN}" || "${REG_TOKEN}" == "null" ]]; then
    echo "ERROR: Failed to obtain runner registration token."
    echo "Check the repository name and PAT permissions."
    exit 1
fi

echo "Registering runner..."

./config.sh \
    --url "https://github.com/${GH_REPO}" \
    --token "${REG_TOKEN}" \
    --name "${RUNNER_NAME}" \
    --labels "${RUNNER_LABELS}" \
    --work "_work" \
    --unattended \
    --replace

echo
echo "Runner registered."
echo "Starting GitHub Actions runner..."
echo

./run.sh &

RUNNER_PID=$!

set +e
wait "${RUNNER_PID}"
EXIT_CODE=$?
set -e

echo
echo "Runner process exited with code ${EXIT_CODE}."

unregister_runner

exit "${EXIT_CODE}"