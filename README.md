# Course Index

A semantic search system for Jeff's teaching materials, built April 2026.

## What It Does

Indexes ~80 course repositories spanning 17+ years of teaching .NET Web API
and Angular development at Progressive Insurance, making them queryable via
natural language through Claude Code.

Instead of remembering which repo has the JWT setup, or which class had the
good Aspire demo, you ask:

> "How do I set up the mock OIDC server I use in my Web API courses?"

And get back your actual code, your actual config, your actual approach —
not a generic answer.

## Why It Exists

Two problems:

1. **Retrieval**: 80+ repos on a NAS is not a knowledge base. It's an
   archive. This makes it queryable.

2. **Context**: Claude gives better answers when grounded in your actual
   patterns and decisions. "Show me how I do X" is a fundamentally different
   question than "show me how to do X."

This is a small instance of a larger thesis being developed under the
"AI Accessibility" label: developer tools should treat the LLM as a
first-class user, not an afterthought.

## Architecture

NAS (/mnt/course-repos/)
└── 80+ course repos
└── \_github/ ← cloned GitHub repos (course guides, projects)

PostgreSQL (Docker, port 5433)
└── pgvector extension
└── course_repos ← one row per repo, with AI-generated metadata
└── course_chunks ← chunked file contents with embeddings

Voyage AI (voyage-code-3)
└── Generates 1024-dim embeddings optimized for code

Anthropic API (claude-haiku)
└── Generates repo summaries and metadata at ingest time

MCP Server (mcp_server.py)
└── Exposes three tools to Claude Code: - search_course_material - list_course_repos - get_repo_summary

## Source Types

| source_type         | Description                                            |
| ------------------- | ------------------------------------------------------ |
| `class_repo`        | A specific class instance, e.g. `web-api-200-feb-2026` |
| `reference_repo`    | Prep/reference material, e.g. `teaching-reference`     |
| `course_guide`      | Astro Starlight sites with TDRs and student resources  |
| `personal_research` | Blog, AI Accessibility projects (Stellar, msw-lens)    |

## Corpus Stats (April 2026)

- 79 repos indexed
- 10,950 chunks
- 28 web_api repos, 26 angular repos, 11 intro, 2 typescript
- Plus 6 GitHub repos: blog, course guide sites, Stellar, msw-lens

## Infrastructure

- **Docker Compose**: pgvector/pgvector:pg16 on port 5433
- **Python 3.14** in `.venv`
- **Key dependencies**: anthropic, voyageai, pgvector, psycopg2-binary,
  tiktoken, pyyaml, mcp

## Running the MCP Server

### Local (this machine)

Registered globally in Claude Code via:

```bash
claude mcp add --scope user --transport stdio course-index \
  /home/jeff/course-index/start_mcp.sh
```

The server starts automatically when Claude Code needs it.

### Network (other Tailscale machines)

Start the HTTP/SSE server on this machine:

```bash
./start_mcp_network.sh
# Listening on http://0.0.0.0:8080/sse
```

On the remote machine, add the MCP server to Claude Code:

```bash
claude mcp add --scope user --transport sse course-index \
  http://<this-machines-tailscale-ip>:8080/sse
```

Or add it manually to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "course-index": {
      "type": "url",
      "url": "http://<this-machines-tailscale-ip>:8080/sse"
    }
  }
}
```

`start_mcp_network.sh` is not persistent across reboots — run it in a terminal
or set up a systemd service if you want it always-on.

## Re-ingesting

To re-ingest everything (only changed files are re-processed):

```bash
cd ~/course-index
source .venv/bin/activate
export $(cat .env | xargs)
python3 -u ingest.py
```

To ingest a single local directory (any directory, not just NAS repos):

```bash
cd ~/course-index && source .venv/bin/activate && export $(cat .env | xargs)
python3 ingest.py --path /path/to/directory
python3 ingest.py --path /path/to/directory --slug my-custom-slug
```

## Adding a New Course Repo

After a class finishes:

```bash
# Clone to NAS
gh repo clone <github-url> /mnt/course-repos/<repo-name> -- --depth=1

# Re-ingest (hash check means only new files are processed)
cd ~/course-index && source .venv/bin/activate && export $(cat .env | xargs)
python3 -u ingest.py
```

## Updating GitHub Repos

```bash
cd /mnt/course-repos/_github
for dir in */; do
    echo "Pulling $dir..."
    git -C "$dir" pull --ff-only
done

# Then re-ingest
cd ~/course-index && source .venv/bin/activate && export $(cat .env | xargs)
python3 -u ingest.py
```

## Files

| File                 | Purpose                                                       |
| -------------------- | ------------------------------------------------------------- |
| `ingest.py`          | Walks repos, chunks files, generates embeddings, stores in DB |
| `mcp_server.py`      | MCP server exposing search tools to Claude Code               |
| `start_mcp.sh`       | Wrapper script that loads env vars and starts MCP server      |
| `config/repos.yaml`  | Repo exclusions, course type patterns, source type overrides  |
| `docker-compose.yml` | pgvector database                                             |
| `.env`               | API keys (never committed)                                    |

## What's Next

- [ ] `finish-course.sh` — one command to clone and ingest a new class repo
- [ ] `refresh-github.sh` — pull and re-ingest all GitHub repos
- [ ] `is_current` flag — mark most recent instance of each course type
- [ ] Recency boost in search results
- [ ] Index this conversation as `personal_research`
- [ ] Blog post

## Origin

Built in a single session, April 3 2026, in a conversation that started
with "would this be a good application of RAG?" and ended with a working
system that answered its own test question with the exact Aspire OIDC setup
from the actual course repos.

The conversation itself is worth reading. It covers RAG vs graph RAG tradeoffs,
chunking strategy for mixed code/markdown corpora, the AI Accessibility thesis,
situated AI vs documentation-answering chatbots, and why the Aspire dashboard
copilot is a missed opportunity.
EOF
