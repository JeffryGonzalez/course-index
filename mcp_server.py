import os
import sys
import json
import psycopg2
import voyageai
from pgvector.psycopg2 import register_vector
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

DB_CONN = "host=localhost port=5433 dbname=courseindex user=courseindex password=courseindex"
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY")
EMBED_MODEL = "voyage-code-3"

app = Server("course-index")

_voyage_client = None

def get_voyage_client():
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
    return _voyage_client

def get_connection():
    conn = psycopg2.connect(DB_CONN)
    register_vector(conn)
    return conn

def embed_query(text):
    vc = get_voyage_client()
    result = vc.embed([text], model=EMBED_MODEL)
    return result.embeddings[0]

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_course_material",
            description="""Search Jeff's teaching materials across all course repositories.
Use this when Jeff asks about code patterns, demos, or examples he has used in past classes.
Examples: 'how do I set up JWT auth', 'show me my Angular signals demo', 
'how have I implemented the BFF pattern', 'what mock OIDC server do I use'""",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of what you're looking for"
                    },
                    "course_type": {
                        "type": "string",
                        "enum": ["angular", "web_api", "typescript", "intro", "any"],
                        "description": "Filter by course type. Use 'any' to search all.",
                        "default": "any"
                    },
                    "chunk_type": {
                        "type": "string", 
                        "enum": ["source_file", "instructor_note", "readme", "changelog", "http_file", "script", "any"],
                        "description": "Filter by content type. Use 'any' to search all.",
                        "default": "any"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 15)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        ),
        types.Tool(
            name="list_course_repos",
            description="""List Jeff's indexed course repositories with their summaries.
Use this to get an overview of available material, or to find repos by course type.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_type": {
                        "type": "string",
                        "enum": ["angular", "web_api", "typescript", "intro", "reference", "any"],
                        "description": "Filter by course type",
                        "default": "any"
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["class_repo", "reference_repo", "course_guide", "personal_research", "any"],
                        "description": "Filter by source type",
                        "default": "any"
                    }
                }
            }
        ),
        types.Tool(
            name="get_repo_summary",
            description="Get the full summary and metadata for a specific repository by slug.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {
                        "type": "string",
                        "description": "The repository slug, e.g. 'web-api-200-feb-2026'"
                    }
                },
                "required": ["repo_slug"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "search_course_material":
        return await search_course_material(arguments)
    elif name == "list_course_repos":
        return await list_course_repos(arguments)
    elif name == "get_repo_summary":
        return await get_repo_summary(arguments)
    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

async def search_course_material(args):
    query = args["query"]
    course_type = args.get("course_type", "any")
    chunk_type = args.get("chunk_type", "any")
    limit = min(args.get("limit", 5), 15)

    query_vector = embed_query(query)
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            filters = []
            params = []

            if course_type != "any":
                filters.append("r.course_type = %s")
                params.append(course_type)

            if chunk_type != "any":
                filters.append("c.chunk_type = %s")
                params.append(chunk_type)

            where_clause = ""
            if filters:
                where_clause = "WHERE " + " AND ".join(filters)

            sql = f"""
                SELECT 
                    r.repo_slug,
                    r.course_type,
                    r.source_type,
                    c.file_path,
                    c.chunk_type,
                    c.content,
                    1 - (c.embedding <=> %s::vector) as similarity
                FROM course_chunks c
                JOIN course_repos r ON r.id = c.repo_id
                {where_clause}
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
            """

            cur.execute(sql, [query_vector] + params + [query_vector, limit])
            rows = cur.fetchall()

    finally:
        conn.close()

    if not rows:
        return [types.TextContent(type="text", text="No results found.")]

    output = [f"Search results for: '{query}'\n"]
    output.append(f"Found {len(rows)} results\n")
    output.append("=" * 60)

    for repo, course_type, source_type, path, chunk_type, content, similarity in rows:
        output.append(f"\nRepo: {repo} ({course_type} / {source_type})")
        output.append(f"File: {path}")
        output.append(f"Type: {chunk_type} | Similarity: {similarity:.3f}")
        output.append(f"\n{content}")
        output.append("\n" + "-" * 40)

    return [types.TextContent(type="text", text="\n".join(output))]

async def list_course_repos(args):
    course_type = args.get("course_type", "any")
    source_type = args.get("source_type", "any")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            filters = []
            params = []

            if course_type != "any":
                filters.append("course_type = %s")
                params.append(course_type)

            if source_type != "any":
                filters.append("source_type = %s")
                params.append(source_type)

            where_clause = ""
            if filters:
                where_clause = "WHERE " + " AND ".join(filters)

            cur.execute(f"""
                SELECT repo_slug, course_type, source_type, 
                       tech_tags, LEFT(summary, 150) as summary,
                       ingest_date
                FROM course_repos
                {where_clause}
                ORDER BY repo_slug
            """, params)

            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return [types.TextContent(type="text", text="No repos found.")]

    output = [f"Course repositories ({len(rows)} total)\n", "=" * 60]

    for slug, c_type, s_type, tech_tags, summary, ingest_date in rows:
        output.append(f"\n{slug}")
        output.append(f"  Type: {c_type} / {s_type}")
        output.append(f"  Tech: {', '.join(tech_tags or [])}")
        output.append(f"  {summary}")

    return [types.TextContent(type="text", text="\n".join(output))]

async def get_repo_summary(args):
    repo_slug = args["repo_slug"]
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT repo_slug, course_type, source_type,
                       tech_tags, topic_tags, summary, ingest_date
                FROM course_repos
                WHERE repo_slug = %s
            """, (repo_slug,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return [types.TextContent(type="text", text=f"Repo '{repo_slug}' not found.")]

    slug, c_type, s_type, tech_tags, topic_tags, summary, ingest_date = row
    output = [
        f"Repository: {slug}",
        f"Course type: {c_type} | Source type: {s_type}",
        f"Indexed: {ingest_date}",
        f"Tech: {', '.join(tech_tags or [])}",
        f"Topics: {', '.join(topic_tags or [])}",
        f"\nSummary:\n{summary}"
    ]

    return [types.TextContent(type="text", text="\n".join(output))]

if __name__ == "__main__":
    import asyncio
    from mcp.server.stdio import stdio_server

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())

    asyncio.run(main())
