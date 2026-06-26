# Clickpost AI Projects

AI-powered backend service that lets Clickpost users ask natural-language questions about their shipment recommendation engine (RR log analyst). Built with FastAPI, Claude (Anthropic), and PostgreSQL.

---

## Table of Contents

- [What This Service Does](#what-this-service-does)
- [High-Level Architecture](#high-level-architecture)
- [Tech Stack and Why](#tech-stack-and-why)
- [Project Structure](#project-structure)
- [Local Development](#local-development)
- [Environment Variables](#environment-variables)
- [Database and Migrations](#database-and-migrations)
- [Deployment](#deployment)
- [API Overview](#api-overview)
- [Adding New Features](#adding-new-features)

---

## What This Service Does

A Clickpost user can send a message like:

> "Why did AWB CP123456789 get assigned to BlueDart instead of Delhivery?"

The service:
1. Authenticates the user via their Clickpost token
2. Fetches the raw RR logs, filter rules, account configs, and audit data from Clickpost's internal APIs
3. Feeds everything to Claude (Anthropic's LLM) along with a knowledge base about the recommendation pipeline
4. Streams Claude's reasoning and final answer back to the browser in real time via SSE (Server-Sent Events)
5. Stores the conversation so the user can ask follow-up questions

---

## High-Level Architecture

```
Browser / Frontend
        │
        │  HTTP POST /api/rr-log-analyst/messages/
        │  Authorization: Token <clickpost_token>
        ▼
┌─────────────────────────────────────────────┐
│              FastAPI (Uvicorn)               │
│                                             │
│  1. Validate Clickpost token                │
│  2. Build system prompt (knowledge base     │
│     + enterprise context)                   │
│  3. Start agent loop (AgentRunner)          │
│                  │                          │
│     ┌────────────▼──────────────┐           │
│     │       Agent Loop          │           │
│     │  Claude ←→ AgentRunner    │           │
│     │                           │           │
│     │  Claude decides:          │           │
│     │    "call fetch_rr_log"    │           │
│     │          ↓                │           │
│     │  AgentRunner calls        │           │
│     │  Clickpost API            │           │
│     │          ↓                │           │
│     │  Result too large?        │           │
│     │    Haiku summarises it    │           │
│     │    (scratchpad)           │           │
│     │          ↓                │           │
│     │  Feed result back         │           │
│     │  to Claude                │           │
│     │          ↓                │           │
│     │  Claude gives final       │           │
│     │  answer → "done"          │           │
│     └────────────┬──────────────┘           │
│                  │                          │
│  4. Stream events to browser (SSE)          │
│     thinking_delta, text_delta,             │
│     tool_start, tool_result, done           │
│                  │                          │
│  5. Save conversation to PostgreSQL         │
└─────────────────────────────────────────────┘
        │                        │
        ▼                        ▼
 Clickpost Internal API    PostgreSQL (DB)
 (fetch logs, rules,       (conversation +
  accounts, etc.)           message history)
```

---

## Tech Stack and Why

### FastAPI
FastAPI is the Python web framework, it handles HTTP requests, routing, and response serialization. We chose it over Django because:
- **Async-first**: our agent loop, Clickpost API calls, and Claude API calls are all async (non-blocking). FastAPI is built for this; Django is sync by default.
- **No bloat**: we don't need Django's admin, ORM, or template engine. FastAPI is lightweight.
- **SSE support**: streaming responses (Server-Sent Events) work cleanly with FastAPI's `StreamingResponse`.

FastAPI does not run itself , **Uvicorn** is the actual web server that listens on port 8000 and hands requests to FastAPI.

### Claude (Anthropic) via LangChain
Claude is the LLM (large language model) , it reads the conversation and Clickpost data, reasons about it, and writes the answer. We use two Claude models:
- **Claude Sonnet** (primary): handles all reasoning, tool decisions, and answer generation
- **Claude Haiku** (secondary): cheap and fast, used only to summarise large tool results into the scratchpad

LangChain is the Python library that provides message types (`HumanMessage`, `AIMessage`, `ToolMessage`) and the tool-calling interface. `ChatAnthropic` is the LangChain adapter for Claude's API.

### PostgreSQL
Stores conversation history so users can continue previous conversations after page refresh or server restart. We use:
- **SQLAlchemy** (async): Python ORM , maps Python classes to DB tables, handles queries
- **asyncpg**: the async PostgreSQL driver (the actual TCP connection to Postgres)
- **Alembic**: migration tool , tracks and applies schema changes without dropping data

### Docker + Docker Compose
The app and database run in separate Docker containers managed by Docker Compose. This ensures the environment is identical between a developer's laptop and the production EC2 server.

---

## Project Structure

```
clickpost-ai-projects/
│
├── app/
│   ├── main.py                     # FastAPI app factory, CORS, lifespan (startup/shutdown)
│   ├── config.py                   # All settings (reads from .env via pydantic-settings)
│   ├── db.py                       # SQLAlchemy async engine, session factory, Base class
│   ├── exceptions.py               # Custom HTTP exceptions (NotFoundError, etc.)
│   │
│   ├── clients/
│   │   ├── clickpost.py            # HTTP client for Clickpost internal APIs (httpx)
│   │   └── llm.py                  # Creates Claude LLM instances (primary + secondary/Haiku)
│   │
│   └── rr_log_analyst/             # The RR log analyst feature
│       ├── auth.py                 # Validates Authorization: Token <clickpost_token> header
│       │
│       ├── agent/
│       │   ├── runner.py           # AgentRunner , the main agent loop (drives Claude)
│       │   ├── prompts.py          # Builds the system prompt (knowledge base + enterprise context)
│       │   ├── scratchpad.py       # Haiku summarises large tool results to save context space
│       │   ├── result_query.py     # Tools for Claude to re-query stored tool results
│       │   └── context.py          # ToolContext dataclass (token, clickpost_client, conversation_id)
│       │
│       ├── tools/                  # All tools Claude can call
│       │   ├── rr_logs.py          # fetch_rr_log , fetches recommendation engine logs
│       │   ├── rules.py            # fetch_filter_rules, fetch_rule_details
│       │   ├── accounts.py         # fetch_account, fetch_enterprise_accounts
│       │   ├── flow_config.py      # fetch_flow_config
│       │   ├── audit_logs.py       # fetch_audit_logs
│       │   ├── recommendation.py   # fetch_recommendation
│       │   ├── allocation_views.py # fetch_allocation_views
│       │   ├── load_distribution.py# fetch_load_distribution
│       │   ├── order_creation_logs.py # fetch_order_creation_logs
│       │   └── result_query.py     # query_tool_result, query_scratchpad_batch (re-read stored results)
│       │
│       ├── memory/
│       │   ├── store.py            # In-memory ConversationStore (to be replaced by PostgreSQL)
│       │   └── tool_results.py     # Stores large raw tool results in memory for Claude to re-query
│       │
│       ├── models/                 # SQLAlchemy ORM models (DB table definitions)
│       │   ├── conversation.py     # conversations table
│       │   └── message.py          # messages table
│       │
│       ├── routers/                # HTTP route handlers
│       │   ├── messages.py         # POST /messages/ , main SSE endpoint (send a message)
│       │   └── conversations.py    # GET/POST /conversations/ , list and fetch conversations
│       │
│       ├── schemas/                # Pydantic request/response schemas (API contract)
│       │   ├── messages.py         # SendMessageRequest
│       │   └── conversations.py    # ConversationSummary, ConversationDetail, etc.
│       │
│       └── knowledge/              # Markdown files injected into Claude's system prompt
│           ├── 01_pipeline_overview.md
│           ├── 02_account_structure.md
│           └── ...                 # One file per domain concept
│
├── alembic/                        # Database migration setup
│   ├── env.py                      # Configures Alembic to use our async engine + models
│   └── versions/                   # Auto-generated migration files (one per schema change)
│
├── alembic.ini                     # Alembic config (points to alembic/ folder)
├── Dockerfile                      # How to build the app Docker image
├── docker-compose.yml              # Defines app + db services for local and production
├── Makefile                        # Shortcuts for common dev commands (see below)
├── build_and_deploy.sh             # Production deployment script (runs on EC2)
├── requirements.txt                # Python dependencies (pinned versions)
├── .env.example                    # Template for required environment variables
└── .gitignore
```

---

## Local Development

### Prerequisites
- Docker and Docker Compose installed
- Python 3.10+ (for running `alembic revision` locally)
- `alembic`, `sqlalchemy`, `asyncpg` installed locally (`pip install alembic sqlalchemy asyncpg`)

### First-time setup

```bash
# 1. Copy env template and fill in values
cp .env.example .env
# Edit .env , set ANTHROPIC_API_KEY, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, DATABASE_URL

# 2. Start everything
make up-d

# 3. Apply database migrations
make migrate
```

### Daily workflow

| Command | When to use |
|---|---|
| `make up` | Start app + db, logs stream to terminal |
| `make up-d` | Start app + db in background |
| `make down` | Stop containers (data preserved) |
| `make build` | Rebuild app image after code changes |
| `make migrate` | Apply pending migrations to DB |
| `make revision name="..."` | Generate new migration after model change |
| `make logs` | Follow live app logs |
| `make shell` | Open bash inside app container |
| `make db-shell` | Open psql inside db container |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key from console.anthropic.com |
| `DATABASE_URL` | Yes | Postgres connection string (`postgresql+asyncpg://user:pass@db:5432/dbname`) |
| `POSTGRES_USER` | Yes | Postgres username (used by docker-compose to create the DB) |
| `POSTGRES_PASSWORD` | Yes | Postgres password |
| `POSTGRES_DB` | Yes | Postgres database name |
| `CLICKPOST_BASE_URL` | No | Clickpost internal API base URL (default: `https://internal.clickpost.in`) |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins (default: `https://alerts.clickpost.in`) |
| `RR_ANALYST_MODEL` | No | Claude model to use (default: `claude-sonnet-4-6`) |
| `RR_ANALYST_THINKING_ENABLED` | No | Enable Claude extended thinking (default: `true`) |

---

### Migration workflow

```bash
# After changing a model (adding/removing column, new table):
make revision name="describe what changed"   # generates migration file
git add alembic/versions/
git commit -m "migration: describe what changed"

# On every deploy (or after pulling new migration files):
make migrate
```

Alembic tracks which migrations have run in the `alembic_version` table. Running `make migrate` is always safe , it only applies migrations that haven't run yet.

---

## Deployment


### Deploying new code

```bash
./build_and_deploy.sh
```

The script:
1. `git pull` — fetches latest code and migration files
2. `docker compose build app` — builds new app image
3. `docker compose run --rm app alembic upgrade head` — applies migrations (safe: old container still running)
4. `docker compose up -d --no-deps app` — replaces only the app container, db untouched

If migrations fail, the script stops and the old container keeps serving traffic.

### EC2 port configuration

| Port | Open to internet? | Why |
|---|---|---|
| 8000 | No | Uvicorn, bound to localhost , only nginx can reach it |
| 5432 | No | Postgres , only app container needs it, stays internal |

---

## Adding New Features

### Add a new tool for Claude to call

1. Create `app/rr_log_analyst/tools/my_tool.py`
2. Define a function decorated with `@tool` (LangChain) , Claude reads the docstring as its description
3. Add the module path to `_TOOL_MODULES` in `app/rr_log_analyst/tools/__init__.py`
4. Claude will automatically discover and use it on the next restart

### Add a new API endpoint

1. Create or edit a file in `app/rr_log_analyst/routers/`
2. Define Pydantic schemas in `app/rr_log_analyst/schemas/`
3. Register the router in `app/routers/__init__.py`

### Add a new DB table

1. Create a model in `app/rr_log_analyst/models/`
2. Import it in `app/rr_log_analyst/models/__init__.py`
3. Run `make revision name="describe the table"` to generate migration
4. Run `make migrate` to apply
5. Commit both the model file and the migration file

### Update Claude's knowledge base

Edit or add `.md` files in `app/rr_log_analyst/knowledge/`. They are loaded into the system prompt on every request. File names are sorted alphabetically , use numeric prefixes (`01_`, `02_`) to control order.
