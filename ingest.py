import os
import re
import hashlib
import yaml
import anthropic
import voyageai
import psycopg2
import tiktoken
from pathlib import Path
from pgvector.psycopg2 import register_vector

# ── Configuration ────────────────────────────────────────────────────────────

DB_CONN = "host=localhost port=5433 dbname=courseindex user=courseindex password=courseindex"
NAS_PATH = "/mnt/course-repos"
CONFIG_PATH = "/home/jeff/course-index/config/repos.yaml"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY")
EMBED_MODEL = "voyage-code-3"
SUMMARY_MODEL = "claude-haiku-4-5-20251001"
MAX_CHUNK_TOKENS = 400
OVERLAP_TOKENS = 50

INCLUDE_EXTENSIONS = {
    ".cs", ".ts", ".md", ".http", ".sh", ".py",
    ".json", ".yaml", ".yml", ".html"
}

EXCLUDE_FILENAME_PATTERNS = [
    "package-lock.json", "*.spec.json", "*.min.js",
    "*.min.css", "appsettings*.json"
]

EXCLUDE_DIRS = {
    ".git", "node_modules", "bin", "obj", ".venv",
    "__pycache__", ".angular", "dist", "coverage"
}

HIGH_VALUE_PATTERNS = {
    "instructor-notes": "instructor_note",
    "instructor_notes": "instructor_note",
    "notes":            "instructor_note",
    "changes.md":       "changelog",
    "summary.md":       "changelog",
    "readme.md":        "readme",
    ".http":            "http_file",
    ".sh":              "script",
}

# ── Helpers ──────────────────────────────────────────────────────────────────

enc = tiktoken.get_encoding("cl100k_base")

def count_tokens(text):
    return len(enc.encode(text))

def chunk_text(text, max_tokens=MAX_CHUNK_TOKENS, overlap=OVERLAP_TOKENS):
    tokens = enc.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk = enc.decode(tokens[start:end])
        chunks.append(chunk)
        if end == len(tokens):
            break
        start += max_tokens - overlap
    return chunks

def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()

def should_exclude_file(path):
    name = path.name.lower()
    for pattern in EXCLUDE_FILENAME_PATTERNS:
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return True
        elif name == pattern:
            return True
    return False

def get_chunk_type(file_path):
    path_str = str(file_path).lower()
    name = file_path.name.lower()
    for pattern, chunk_type in HIGH_VALUE_PATTERNS.items():
        if pattern in path_str or name == pattern:
            return chunk_type
    return "source_file"

def infer_source_type(repo_slug, overrides, patterns):
    if repo_slug in overrides:
        return overrides[repo_slug]
    slug_lower = repo_slug.lower()
    reference_hints = ["prep", "reference", "notes", "teacher", "demo", "starter"]
    for hint in reference_hints:
        if hint in slug_lower:
            return "reference_repo"
    return "class_repo"

# ── Embedding ─────────────────────────────────────────────────────────────────

_voyage_client = None

def get_voyage_client():
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
    return _voyage_client

def embed(texts):
    vc = get_voyage_client()
    result = vc.embed(texts, model=EMBED_MODEL)
    return result.embeddings

# ── Summarization ─────────────────────────────────────────────────────────────

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def generate_repo_summary(repo_slug, file_tree, sample_content):
    prompt = f"""You are helping build a searchable index of a software developer's teaching materials.

Analyze this repository and provide a structured summary.

Repository name: {repo_slug}

File tree:
{file_tree}

Sample content from key files:
{sample_content}

Respond with a JSON object with these exact fields:
{{
  "summary": "2-3 sentence description of what this repo teaches and demonstrates",
  "tech_tags": ["list", "of", "technologies", "used"],
  "topic_tags": ["list", "of", "concepts", "covered"],
  "course_type": "angular|web_api|typescript|intro|reference|unknown"
}}

Respond with only the JSON object, no other text."""

    message = anthropic_client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    import json
    text = message.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    return json.loads(text)

# ── Database ──────────────────────────────────────────────────────────────────

def get_connection():
    conn = psycopg2.connect(DB_CONN)
    register_vector(conn)
    return conn

def get_existing_hashes(conn, repo_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT file_path, ingest_hash FROM course_chunks WHERE repo_id = %s",
            (repo_id,)
        )
        return {row[0]: row[1] for row in cur.fetchall()}

def upsert_repo(conn, repo_slug, source_type, course_type, summary, tech_tags, topic_tags):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO course_repos (repo_slug, source_type, course_type, summary, tech_tags, topic_tags)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (repo_slug) DO UPDATE SET
                source_type = EXCLUDED.source_type,
                course_type = EXCLUDED.course_type,
                summary     = EXCLUDED.summary,
                tech_tags   = EXCLUDED.tech_tags,
                topic_tags  = EXCLUDED.topic_tags,
                ingest_date = now()
            RETURNING id
        """, (repo_slug, source_type, course_type, summary, tech_tags, topic_tags))
        return cur.fetchone()[0]

def insert_chunk(conn, repo_id, chunk_type, file_path, content, embedding, ingest_hash):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO course_chunks (repo_id, chunk_type, file_path, content, embedding, ingest_hash)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (repo_id, chunk_type, str(file_path), content, embedding, ingest_hash))

def delete_chunks_for_file(conn, repo_id, file_path):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM course_chunks WHERE repo_id = %s AND file_path = %s",
            (repo_id, str(file_path))
        )

# ── Main ingest ───────────────────────────────────────────────────────────────

def collect_files(repo_path):
    files = []
    for path in Path(repo_path).rglob("*"):
        if path.is_file():
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if path.suffix not in INCLUDE_EXTENSIONS:
                continue
            if should_exclude_file(path):
                continue
            files.append(path)
    return files

def build_file_tree(repo_path, files):
    rel_paths = sorted(str(f.relative_to(repo_path)) for f in files)
    return "\n".join(rel_paths[:100])

def sample_key_files(repo_path, files):
    samples = []
    priority = ["readme.md", "changes.md", "summary.md"]

    selected = []
    for name in priority:
        for f in files:
            if f.name.lower() == name:
                selected.append(f)
                break

    source_files = [f for f in files if f.suffix in {".cs", ".ts"}
                    and "spec" not in f.name.lower()][:2]
    selected.extend(source_files)

    for f in selected:
        try:
            content = f.read_text(errors="ignore")[:1500]
            rel = f.relative_to(repo_path)
            samples.append(f"--- {rel} ---\n{content}")
        except Exception:
            pass

    return "\n\n".join(samples)[:4000]

def ingest_repo(repo_slug, repo_path, config, conn):
    print(f"\n{'='*60}")
    print(f"Ingesting: {repo_slug}")

    files = collect_files(repo_path)
    if not files:
        print(f"  No eligible files found, skipping.")
        return

    print(f"  Found {len(files)} eligible files")
    print(f"  Generating summary...")

    file_tree = build_file_tree(repo_path, files)
    sample_content = sample_key_files(repo_path, files)

    try:
        meta = generate_repo_summary(repo_slug, file_tree, sample_content)
    except Exception as e:
        print(f"  Summary generation failed: {e}, using defaults")
        meta = {
            "summary": f"Repository: {repo_slug}",
            "tech_tags": [],
            "topic_tags": [],
            "course_type": "unknown"
        }

    source_type = infer_source_type(
        repo_slug,
        config.get("source_type_overrides", {}),
        config.get("course_patterns", {})
    )

    print(f"  Source type: {source_type} | Course type: {meta['course_type']}")
    print(f"  Summary: {meta['summary'][:80]}...")

    repo_id = upsert_repo(
        conn, repo_slug, source_type,
        meta["course_type"], meta["summary"],
        meta.get("tech_tags", []), meta.get("topic_tags", [])
    )

    existing_hashes = get_existing_hashes(conn, repo_id)

    new_files = 0
    updated_files = 0
    skipped_files = 0
    total_chunks = 0

    for file_path in files:
        try:
            content = file_path.read_text(errors="ignore")
            if not content.strip():
                continue

            rel_path = str(file_path.relative_to(repo_path))
            content_hash = sha256(content)

            if existing_hashes.get(rel_path) == content_hash:
                skipped_files += 1
                continue

            if rel_path in existing_hashes:
                delete_chunks_for_file(conn, repo_id, rel_path)
                updated_files += 1
            else:
                new_files += 1

            chunk_type = get_chunk_type(file_path)
            chunks = chunk_text(content)
            vectors = embed(chunks)

            for chunk_content, vector in zip(chunks, vectors):
                insert_chunk(
                    conn, repo_id, chunk_type,
                    rel_path, chunk_content,
                    vector, content_hash
                )
                total_chunks += 1

        except Exception as e:
            print(f"  Error processing {file_path}: {e}")
            continue

    conn.commit()
    print(f"  New: {new_files} | Updated: {updated_files} | Skipped: {skipped_files} | Chunks: {total_chunks}")

def main():
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    exclude = set(config.get("exclude_repos", []))
    nas_path = Path(config["nas_path"])

    conn = get_connection()

    repo_dirs = [d for d in nas_path.iterdir()
                 if d.is_dir() and d.name not in exclude and not d.name.startswith(".")]

    repo_dirs.sort(key=lambda d: d.name)

    print(f"Found {len(repo_dirs)} repos to process")

    for repo_dir in repo_dirs:
        ingest_repo(repo_dir.name, repo_dir, config, conn)

    conn.close()
    print(f"\nIngest complete.")

if __name__ == "__main__":
    main()