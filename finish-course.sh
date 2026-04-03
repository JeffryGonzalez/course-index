#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: finish-course.sh <github-url> [repo-name]"
    echo "Example: finish-course.sh https://github.com/JeffryGonzalez/web-api-100-mar-2026"
    exit 1
fi

GITHUB_URL="$1"
REPO_NAME="${2:-$(basename $1)}"
NAS_PATH="/mnt/course-repos"
COURSE_INDEX="/home/jeff/course-index"

echo "================================================"
echo "Finishing course: $REPO_NAME"
echo "================================================"

# Clone to NAS
if [ -d "$NAS_PATH/$REPO_NAME" ]; then
    echo "→ Repo already exists on NAS, pulling latest..."
    git -C "$NAS_PATH/$REPO_NAME" pull --ff-only
else
    echo "→ Cloning to NAS..."
    gh repo clone "$GITHUB_URL" "$NAS_PATH/$REPO_NAME" -- --depth=1
fi

# Activate venv and load env
source "$COURSE_INDEX/.venv/bin/activate"
export $(cat "$COURSE_INDEX/.env" | xargs)

# Ingest just this repo
echo "→ Ingesting..."
python3 - << PYEOF
from pathlib import Path
import yaml, sys
sys.path.insert(0, '$COURSE_INDEX')
import ingest

with open(ingest.CONFIG_PATH) as f:
    config = yaml.safe_load(f)

conn = ingest.get_connection()
ingest.ingest_repo("$REPO_NAME", Path("$NAS_PATH/$REPO_NAME"), config, conn)
conn.close()
PYEOF

echo "================================================"
echo "Done! $REPO_NAME is now indexed."
echo "================================================"
