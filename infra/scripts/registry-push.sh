#!/usr/bin/env bash
# Build + push immutable Kairos images to the OCI container registry (GHCR).
#
# STANDARD push is the GitHub Actions workflow .github/workflows/ci-build-push.yml
# (GITHUB_TOKEN, no password in repo). This script is the manual/dev equivalent:
# it requires a local `docker login ghcr.io` (credentials stored by Docker in
# ~/.docker/config.json) — NEVER from env/scripts/Git.
#
# Usage (from repository root):
#   REGISTRY=ghcr.io NAMESPACE=gehrmannmerlin \
#   RELEASE_VERSION=v0.1.2 ./infra/scripts/registry-push.sh
#
# RELEASE_VERSION empty => immutable tag is the 12-hex git sha only.
# Optional: PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple (local builds).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

REGISTRY="${REGISTRY:?REGISTRY required (e.g. kairos.cn-hongkong.cr.aliyuncs.com)}"
NAMESPACE="${NAMESPACE:?NAMESPACE required (e.g. kairos)}"
RELEASE_VERSION="${RELEASE_VERSION:-}"
PLATFORM="${PLATFORM:-linux/amd64}"   # server arch confirmed x86_64
PIP_INDEX_URL="${PIP_INDEX_URL:-}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

SHA="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
TAG="$SHA"
[ -n "$RELEASE_VERSION" ] && TAG="${RELEASE_VERSION}-${SHA}"

PIP_ARGS=()
[ -n "$PIP_INDEX_URL" ] && PIP_ARGS+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")

declare -a NAMES=(web api worker)
declare -a IMAGES=()
for n in "${NAMES[@]}"; do IMAGES+=("${REGISTRY}/${NAMESPACE}/kairos-${n}:${TAG}"); done

echo "==> building immutable images (release=$TAG platform=$PLATFORM)"
docker buildx build --platform "$PLATFORM" --load -t "${IMAGES[0]}" "$ROOT/frontend/" \
  || fail "web image build"
docker buildx build --platform "$PLATFORM" --load "${PIP_ARGS[@]}" -t "${IMAGES[1]}" "$ROOT/backend/" \
  || fail "api image build"
docker buildx build --platform "$PLATFORM" --load "${PIP_ARGS[@]}" \
  --build-arg KAIROS_INCLUDE_BROWSER=1 -t "${IMAGES[2]}" "$ROOT/backend/" \
  || fail "worker image build"

echo "==> pushing (requires local docker login to $REGISTRY)"
for img in "${IMAGES[@]}"; do
  echo "    push $img"
  docker push "$img" || fail "docker push failed: $img"
done

echo "==> image digests (record these in the release manifest)"
for img in "${IMAGES[@]}"; do
  docker image inspect --format '{{.Name}} {{.Id}}' "$img"
done

echo "REGISTRY_PUSH_OK registry=$REGISTRY namespace=$NAMESPACE tag=$TAG"
