#!/bin/bash
set -e

GITHUB_PATH="/mnt/course-repos/_github"
COURSE_INDEX="/home/jeff/course-index"

echo "================================================"
echo "Refreshing GitHub repos"
echo "================================================"

# Pull latest for all repos in _github
for dir in "$GITHUB_PATH"/*/; do
    repo_name=$(basename "$dir")
    echo "→ Pulling $repo_name..."
    git -C "$dir" pull --ff-only 2>&1 | tail -1
done

echo ""
echo "→ Re-ingesting changed files..."

# Activate venv and load env
source "$COURSE_INDEX/.venv/bin/activate"
export $(cat "$COURSE_INDEX/.env" | xargs)

# Ingest all _github repos
python3 - << 'PYEOF'
from pathlib import Path
import yaml, sys
sys.path.insert(0, '/home/jeff/course-index')
import ingest

with open(ingest.CONFIG_PATH) as f:
    config = yaml.safe_load(f)

github_path = Path("/mnt/course-repos/_github")
conn = ingest.get_connection()

repos = sorted([d for d in github_path.iterdir() if d.is_dir()])
print(f"Found {len(repos)} GitHub repos")

for repo_dir in repos:
    ingest.ingest_repo(repo_dir.name, repo_dir, config, conn)

conn.close()
PYEOF

echo "================================================"
echo "Refresh complete."
echo "================================================"
