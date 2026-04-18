# Project Scaffold Reference

Use this reference when creating a new Zeeker project from scratch. The Zeeker CLI does most
of the work — the skill customizes what it generates.

## When to Use

- The user says "create a new project" or "start from scratch"
- There's no `zeeker.toml` in the working directory
- The user wants to set up a new database for a different data domain

## Step 1 — Run `zeeker init`

The Zeeker CLI creates the full project scaffold:

```bash
uv run zeeker init <project-name>
```

This creates:

```
<project-name>/
├── pyproject.toml              # Dependencies (zeeker only, needs customization)
├── zeeker.toml                 # Project config (generic, needs customization)
├── resources/
│   └── __init__.py             # Empty package
├── .github/
│   └── workflows/
│       └── deploy.yml          # Basic deploy workflow (needs customization)
├── .gitignore                  # Standard Python ignores
├── CLAUDE.md                   # Dev guide (generic, updated by zeeker add)
└── README.md                   # Project readme (generic)
```

Zeeker also runs `uv sync` automatically to create the virtual environment.

## Step 2 — Customize the Generated Files

After `zeeker init`, customize these files for the specific data domain:

### pyproject.toml — Add Source-Specific Dependencies

Zeeker generates a minimal `pyproject.toml` with only `zeeker` as a dependency. Add the
packages needed for the source types you'll be scraping. Run `uv add` for each:

```bash
# For RSS feed resources
uv add feedparser httpx tenacity openai

# For web scraping resources
uv add httpx beautifulsoup4 lxml tenacity

# For PDF extraction via Docling Serve (no extra deps — it's a REST API)
uv add httpx tenacity

# For resources with AI summaries or filtering
uv add openai

# Common testing dependencies
uv add --group dev pytest pytest-asyncio pytest-dotenv
```

### zeeker.toml — Update Project Metadata

Zeeker generates placeholder metadata. Update it with actual project information:

```toml
[project]
name = "<project-name>"
database = "<project-name>.db"
title = "<Human-Readable Project Title>"
description = "<What this database contains and who it's for>"
license = "MIT"
license_url = "https://opensource.org/licenses/MIT"
source = "<Primary source URL>"
```

### .env.example — Create This File

Zeeker does NOT generate `.env.example`. Create it based on the anticipated source types:

```bash
# =============================================================================
# Environment Variables for <project-name>
# =============================================================================
# Copy this file to .env and fill in your actual values.
# Never commit .env to version control.

# === Content Extraction ===

# Docling Serve — PDF and complex document conversion
# Run locally: docker run -p 5001:5001 quay.io/docling-project/docling-serve
# DOCLING_SERVE_URL=http://localhost:5001
# DOCLING_SERVE_API_KEY=

# Jina Reader — article content extraction from URLs
# Get API key at: https://jina.ai/reader/
# JINA_API_TOKEN=

# === LLM Server (summaries, filtering, classification) ===

# Any OpenAI-compatible API: local (Ollama, vLLM, llama.cpp, LM Studio)
# or cloud (OpenAI, Together, Groq, Anthropic-compatible proxies)
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_API_KEY=
# LLM_MODEL=llama3.1

# === Deployment (Zeeker Deploy) ===

# S3_BUCKET=
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
# S3_ENDPOINT_URL=                    # Optional, for non-AWS S3 services
```

Uncomment only the variables that the project's resources actually need.

### .gitignore — Add Checkpoint Files

Zeeker's generated `.gitignore` covers `.db`, `.venv`, `.env`, and `__pycache__`. Add
checkpoint files for resources that use the batch crawl pattern:

```
# Crawl checkpoints (temporary)
checkpoint_*.json
```

### .github/workflows/ — Replace or Augment the Deploy Workflow

Zeeker generates a basic `deploy.yml` that builds and deploys on push to main. For most
projects, you'll want to replace this with cadence-specific workflows generated per resource.
See `deploy-reference.md` for the templates.

### CLAUDE.md — Updated Automatically

Zeeker's `zeeker add` command automatically updates CLAUDE.md with resource documentation.
The skill should add source-specific notes (data source URLs, cadence, environment variables)
after the auto-generated content.

## Step 3 — Add Resources Using `zeeker add`

Do NOT manually create resource files. Use the CLI — it creates the module template, updates
`zeeker.toml`, and updates `CLAUDE.md` in one command. See the resource generation flow in
SKILL.md Step 5.

## Project Naming Conventions

- **Directory name**: lowercase, hyphens for separators (e.g., `singapore-legal-data`)
- **Database name**: Zeeker derives this from the project name (same with `.db` extension)
- **Display name**: Zeeker generates title case from the project name (customizable in zeeker.toml)

## After Scaffolding

Remind the user:

1. `cd <project-name>` (Zeeker prints this)
2. Add dependencies: `uv add <packages>` (based on source types)
3. Create `.env.example` and `.env`
4. Proceed to add resources with `zeeker add`
