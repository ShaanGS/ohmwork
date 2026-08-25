#!/usr/bin/env bash
#
# Deploy the committed tree to a Hugging Face Space.
#
# WHY `git archive HEAD` AND NOT A COPY OF THE WORKING DIRECTORY. Only
# committed, tracked files are shipped. `.env` is gitignored, so it cannot be
# swept into a public Space by a copy that was not paying attention -- which
# is the failure mode that leaks a key, and it leaks it publicly and
# permanently.
#
# Usage:
#   HF_TOKEN=hf_xxx deploy/push-space.sh ShaanGS/ohmwork
#
set -euo pipefail

SPACE="${1:-${HF_SPACE:-}}"
if [[ -z "$SPACE" ]]; then
  echo "usage: HF_TOKEN=hf_xxx $0 <user>/<space>" >&2
  exit 2
fi
if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "HF_TOKEN is not set. Make one at https://huggingface.co/settings/tokens" >&2
  echo "with WRITE access, and keep it out of this repository." >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

git -C "$ROOT" archive HEAD | tar -x -C "$STAGE"

# The Space needs its own README: Hugging Face reads the sdk and the port out
# of the YAML front matter, and the repository's README is for humans reading
# the project rather than for a deployment target.
cp "$ROOT/deploy/space/README.md" "$STAGE/README.md"

cd "$STAGE"
git init -q -b main
git add -A
git -c user.email=deploy@ohmwork -c user.name=deploy commit -qm "deploy $(git -C "$ROOT" rev-parse --short HEAD)"
git push -q --force "https://user:${HF_TOKEN}@huggingface.co/spaces/${SPACE}" main

echo "pushed to https://huggingface.co/spaces/${SPACE}"
echo
echo "The Space will build the Dockerfile. It is NOT usable until these are"
echo "set under Settings -> Variables and secrets:"
echo "  OHMWORK_PASSWORD   (required -- the server refuses to start without it)"
echo "  GROQ_API_KEY, CEREBRAS_API_KEY, GEMINI_API_KEY, ...  (at least one)"
