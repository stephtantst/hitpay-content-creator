# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# CLI (main entry point)
python main.py generate "keyword"        # generate a blog post
python main.py list --status writing     # list posts by status
python main.py export --all              # export all posts to Markdown/CSV
python main.py factcheck POST_ID         # AI fact-check a post
python main.py research                  # analyze competitor content

# Web server
python api.py                            # FastAPI server (Google OAuth + REST API)
```

## Architecture

**Dual interface**: Click CLI (`main.py` → `src/cli.py`) and FastAPI web server (`api.py`) share the same core logic.

**Generation pipeline** (`src/generator.py`):
1. Loads HitPay product knowledge from `hitpay_docs.md`
2. Queries live HitPay MCP server (`HITPAY_MCP_URL`) for knowledge/changelog/news
3. Pulls competitor insights from `src/competitor_db.py`
4. Selects market-specific external links (`external_links_db.json`, keyed by SG/MY/PH/SEA)
5. Calls Claude Sonnet via OpenRouter (`src/llm_client.py`) with streaming + exponential backoff retry
6. Returns structured dict: title, slug, meta_title, meta_description, content, categories, tags

**Post lifecycle**: `writing` → `ready_to_publish` → `published` (file-based in `posts/{status}/`, also tracked in Supabase via `src/database.py`)

**Key modules**:
- `src/generator.py` — main generation logic + Claude API call
- `src/llm_client.py` — OpenRouter client shaped like the Anthropic Messages API (system=, messages=, .content[0].text, .stop_reason, .usage) so call sites didn't need to change when the app moved off calling Anthropic directly
- `src/mcp_client.py` — HitPay knowledge MCP integration
- `src/competitor_db.py` — external link library by market
- `src/fact_checker.py` — AI fact validation
- `src/repurposer.py` — social/email variants from posts
- `config.py` — all env vars and constants

## Key Config (`config.py`)

| Var | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API key — all Claude calls are routed through OpenRouter's OpenAI-compatible endpoint, not Anthropic directly |
| `HITPAY_MCP_URL` | HitPay knowledge MCP server |
| `DATABASE_URL` | Supabase PostgreSQL (pg8000) |
| `GOOGLE_CLIENT_ID/SECRET` | OAuth for web UI |

`POSTS_DIR` auto-detects: Railway (`/data/posts`) → Vercel (`/tmp/posts`) → local (`posts/`)

## Markets

Posts target SG, MY, or PH. Market determines payment method copy, rates, and external link selection. Verified facts per market are in `hitpay_docs.md` and the memory system (`~/.claude/projects/.../memory/`).
