#!/usr/bin/env python3
"""FastAPI backend for HitPay Blog Post Generator UI."""

import asyncio
import csv
import json
import os
import secrets
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import random

from config import (
    ALLOWED_DOMAIN,
    AUTOMATION_SECRET,
    BASE_URL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    POSTS_DIR,
    SECRET_KEY,
)
from src.database import (
    delete_post,
    get_audit_log,
    get_post,
    get_post_by_slug,
    get_repurposed_content,
    init_db,
    migrate_brand_column,
    migrate_x_repurposed_column,
    migrate_source_blog_post_id,
    migrate_source_column,
    migrate_youtube_descriptions_table,
    migrate_youtube_title_column,
    list_feedback,
    list_logins,
    list_posts,
    log_audit,
    log_login,
    save_feedback,
    save_post,
    update_post_fields,
    update_post_status,
    update_repurposed_content,
)
from src.generator import generate_blog_post, rewrite_blog_post
from src.youtube_database import (
    delete_youtube_description,
    get_youtube_description,
    list_youtube_descriptions,
    save_youtube_description,
    update_youtube_description,
)
from src.post_writer import (
    export_bulk_to_csv,
    export_to_csv,
    move_post_file,
    parse_framer_csv,
    read_post_content,
    update_post_file,
    write_post_file,
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    for migrate in (migrate_brand_column, migrate_x_repurposed_column, migrate_source_blog_post_id, migrate_source_column, migrate_youtube_descriptions_table, migrate_youtube_title_column):
        try:
            migrate()
        except Exception:
            pass  # column may already exist; non-fatal
    for d in ["generated", "editing", "ready_to_publish", "published", "exports"]:
        try:
            Path(POSTS_DIR, d).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    yield


app = FastAPI(title="HitPay Blog Generator", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    https_only=BASE_URL.startswith("https://"),
    max_age=86400 * 30,  # 30 days
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Auth ───────────────────────────────────────────────────────────────────────

def require_auth(request: Request) -> str:
    """Dependency: returns email of authenticated user or raises 401."""
    email = request.session.get("email")
    if not email:
        raise HTTPException(401, "Not authenticated")
    return email


GOOGLE_OAUTH_SCOPE = "openid email profile https://www.googleapis.com/auth/analytics.readonly"


@app.get("/auth/login")
def auth_login(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google OAuth is not configured (GOOGLE_CLIENT_ID missing)")
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/auth/callback",
        "response_type": "code",
        "scope": GOOGLE_OAUTH_SCOPE,
        "state": state,
        "access_type": "offline",
        # Force the consent screen every time so Google always re-issues a
        # refresh token (it's only granted on the first consent otherwise).
        "prompt": "consent",
    }
    return RedirectResponse(GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params))


@app.get("/auth/callback")
async def auth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
):
    if error or not code:
        return RedirectResponse("/?auth_error=1")

    expected = request.session.pop("oauth_state", None)
    if not expected or state != expected:
        return RedirectResponse("/?auth_error=1")

    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": f"{BASE_URL}/auth/callback",
                "grant_type": "authorization_code",
            },
        )
        if token_res.status_code != 200:
            return RedirectResponse("/?auth_error=1")

        token_data = token_res.json()
        access_token = token_data.get("access_token")
        userinfo_res = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_res.status_code != 200:
            return RedirectResponse("/?auth_error=1")

    user = userinfo_res.json()
    email = user.get("email", "").lower()

    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        return RedirectResponse("/?auth_error=domain")

    request.session["email"] = email
    request.session["name"] = user.get("name", "")
    # Stashed for GA4 Data API calls (see get_ga_access_token below) — same
    # pattern geo-tracker uses, just stored in this app's own session instead
    # of a NextAuth JWT.
    request.session["ga_access_token"] = access_token
    request.session["ga_token_expires_at"] = time.time() + token_data.get("expires_in", 3600)
    if token_data.get("refresh_token"):
        request.session["ga_refresh_token"] = token_data["refresh_token"]
    try:
        log_login(email, user.get("name", ""))
    except Exception:
        pass  # Never block login due to logging failure
    return RedirectResponse("/")


async def get_ga_access_token(request: Request) -> str | None:
    """Return a valid Google OAuth access token for GA4 calls, refreshing if expired.

    Returns None if the user hasn't (re-)logged in since this feature shipped
    (no refresh token stored yet) or refresh fails — caller should surface a
    "please sign in again" message rather than crash.
    """
    access_token = request.session.get("ga_access_token")
    expires_at = request.session.get("ga_token_expires_at", 0)
    if access_token and time.time() < expires_at - 60:
        return access_token

    refresh_token = request.session.get("ga_refresh_token")
    if not refresh_token:
        return None

    async with httpx.AsyncClient() as client:
        res = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if res.status_code != 200:
        return None

    data = res.json()
    request.session["ga_access_token"] = data.get("access_token")
    request.session["ga_token_expires_at"] = time.time() + data.get("expires_in", 3600)
    return data.get("access_token")


@app.get("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@app.get("/auth/me")
def auth_me(request: Request):
    email = request.session.get("email")
    if not email:
        raise HTTPException(401, "Not authenticated")
    return {"email": email, "name": request.session.get("name", "")}


# ── Root ───────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse("static/index.html")


# ── Posts ─────────────────────────────────────────────────────────────────────

def _serialise(post: dict) -> dict:
    post = dict(post)
    post["categories"] = json.loads(post.get("categories") or "[]")
    post["tags"] = json.loads(post.get("tags") or "[]")
    return post


def _serialise_with_content(post: dict) -> dict:
    post = _serialise(post)
    # Prefer content stored in DB; fall back to file for local legacy posts
    if post.get("content"):
        return post
    file_path = post.get("file_path", "")
    post["content"] = read_post_content(file_path) if file_path and os.path.exists(file_path) else ""
    return post


@app.get("/api/posts")
def api_list_posts(status: str = None, brand: str = None, _: str = Depends(require_auth)):
    posts = list_posts(
        status if status and status != "all" else None,
        brand if brand and brand != "all" else None,
    )
    return [_serialise(p) for p in posts]


@app.get("/api/posts/{post_id}")
def api_get_post(post_id: int, _: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")
    return _serialise_with_content(post)


class UpdatePostRequest(BaseModel):
    title: str | None = None
    slug: str | None = None
    meta_title: str | None = None
    meta_description: str | None = None
    overview: str | None = None
    categories: list[str] | None = None
    tags: list[str] | None = None
    date: str | None = None
    country: str | None = None
    content: str | None = None


@app.put("/api/posts/{post_id}")
def api_update_post(post_id: int, body: UpdatePostRequest, user_email: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    db_fields = {}
    file_updates = {}
    changed_fields = []

    for field in ("title", "slug", "meta_title", "meta_description", "overview", "date", "country"):
        val = getattr(body, field)
        if val is not None:
            db_fields[field] = val
            file_updates[field] = val
            changed_fields.append(field)

    if body.categories is not None:
        db_fields["categories"] = json.dumps(body.categories)
        file_updates["categories"] = body.categories
        changed_fields.append("categories")

    if body.tags is not None:
        db_fields["tags"] = json.dumps(body.tags)
        file_updates["tags"] = body.tags
        changed_fields.append("tags")

    if body.content is not None:
        word_count = len(body.content.split())
        db_fields["word_count"] = word_count
        db_fields["content"] = body.content
        changed_fields.append("content")

    if db_fields:
        update_post_fields(post_id, db_fields)

    file_path = post.get("file_path", "")
    if file_path and os.path.exists(file_path):
        if file_updates:
            update_post_file(file_path, file_updates)
        if body.content is not None:
            _rewrite_content(file_path, body.content)

    if changed_fields:
        log_audit(post_id, user_email, "edited", {"fields": changed_fields})

    return {"ok": True}


def _rewrite_content(file_path: str, new_content: str):
    """Replace the body section of a markdown file, preserving frontmatter."""
    import yaml
    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()

    frontmatter_dict = {}
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            frontmatter_dict = yaml.safe_load(parts[1]) or {}

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        yaml.dump(frontmatter_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        f.write("---\n\n")
        f.write(new_content)


class StatusRequest(BaseModel):
    status: str
    editor_email: str | None = None


class EditorRequest(BaseModel):
    editor_email: str


@app.post("/api/posts/{post_id}/status")
def api_change_status(
    post_id: int,
    body: StatusRequest,
    background_tasks: BackgroundTasks,
    user_email: str = Depends(require_auth),
):
    valid = ["generated", "editing", "ready_to_publish", "published"]
    if body.status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {valid}")

    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    old_file = post.get("file_path", "")
    old_status = post.get("status", "")
    slug = post["slug"]

    if old_file and os.path.exists(old_file):
        new_file = move_post_file(old_file, body.status, slug)
    else:
        new_file = str(Path(POSTS_DIR) / body.status / f"{slug}.md")

    editor_email = body.editor_email if body.status == "editing" else None
    update_post_status(post_id, body.status, old_file, new_file, editor_email=editor_email)
    log_audit(post_id, user_email, "status_changed", {"from": old_status, "to": body.status})

    # Auto-repurpose when a post is published for the first time
    if body.status == "published" and old_status != "published":
        def _bg_repurpose():
            from src.repurpose_scheduler import repurpose_and_schedule
            updated_post = get_post(post_id) or post
            result = repurpose_and_schedule(updated_post, user_email)
            log_audit(post_id, user_email, "auto_repurposed", {
                "date": result["date"].isoformat() if result.get("date") else None,
                "x_id": result.get("x_id"),
                "threads_id": result.get("threads_id"),
                "linkedin_id": result.get("linkedin_id"),
                "errors": result.get("errors") or None,
            })
        background_tasks.add_task(_bg_repurpose)

    return {"ok": True, "file_path": new_file}


@app.patch("/api/posts/{post_id}/editor")
def api_set_editor(post_id: int, body: EditorRequest, _: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")
    update_post_fields(post_id, {"editor_email": body.editor_email})
    return {"ok": True}


@app.delete("/api/posts/{post_id}")
def api_delete_post(post_id: int, user_email: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    file_path = post.get("file_path", "")
    log_audit(post_id, user_email, "deleted", {"title": post.get("title", "")})
    delete_post(post_id)
    if file_path and os.path.exists(file_path):
        os.remove(file_path)

    return {"ok": True}


@app.get("/api/posts/{post_id}/export")
def api_export_post(post_id: int, _: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    file_path = post.get("file_path", "")
    csv_path = export_to_csv(post, file_path)
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"{post['slug']}.csv",
    )


@app.post("/api/posts/{post_id}/import")
async def api_import_post(post_id: int, file: UploadFile = File(...), user_email: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    raw = await file.read()
    try:
        parsed = parse_framer_csv(raw)
    except UnicodeDecodeError:
        raise HTTPException(400, "Could not read file — please upload a CSV exported from this tool")
    except csv.Error as e:
        raise HTTPException(400, f"Could not parse CSV: {e}")

    if not parsed:
        raise HTTPException(400, "CSV file has no data rows")

    body = UpdatePostRequest(**parsed)
    result = api_update_post(post_id, body, user_email)
    log_audit(post_id, user_email, "imported_csv", {"filename": file.filename})
    return result


@app.get("/api/posts/{post_id}/audit-log")
def api_get_audit_log(post_id: int, _: str = Depends(require_auth)):
    return get_audit_log(post_id)


# ── Bulk Export ───────────────────────────────────────────────────────────────

class BulkExportRequest(BaseModel):
    post_ids: list[int]


@app.post("/api/posts/bulk-export")
def api_bulk_export(body: BulkExportRequest, _: str = Depends(require_auth)):
    if not body.post_ids:
        raise HTTPException(400, "No post IDs provided")

    posts_with_paths = []
    for pid in body.post_ids:
        post = get_post(pid)
        if post:
            file_path = post.get("file_path", "") or ""
            posts_with_paths.append((post, file_path if os.path.exists(file_path) else ""))

    if not posts_with_paths:
        raise HTTPException(400, "No valid posts found to export")

    from datetime import date as _date
    csv_path = export_bulk_to_csv(posts_with_paths)
    filename = f"bulk-export-{_date.today().isoformat()}.csv"
    return FileResponse(csv_path, media_type="text/csv", filename=filename)


# ── Bulk Status Change ───────────────────────────────────────────────────────

class BulkStatusRequest(BaseModel):
    post_ids: list[int]
    status: str


@app.post("/api/posts/bulk-status")
def api_bulk_status(body: BulkStatusRequest, user_email: str = Depends(require_auth)):
    valid = ["generated", "editing", "ready_to_publish", "published"]
    if body.status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {valid}")
    if not body.post_ids:
        raise HTTPException(400, "No post IDs provided")

    updated = 0
    for pid in body.post_ids:
        post = get_post(pid)
        if not post:
            continue
        old_file = post.get("file_path", "")
        old_status = post.get("status", "")
        slug = post["slug"]

        if old_file and os.path.exists(old_file):
            new_file = move_post_file(old_file, body.status, slug)
        else:
            new_file = str(Path(POSTS_DIR) / body.status / f"{slug}.md")

        update_post_status(pid, body.status, old_file, new_file)
        log_audit(pid, user_email, "status_changed", {"from": old_status, "to": body.status})
        updated += 1

    return {"ok": True, "updated": updated}


# ── Bulk Delete ──────────────────────────────────────────────────────────────

@app.post("/api/posts/bulk-delete")
def api_bulk_delete(body: BulkExportRequest, user_email: str = Depends(require_auth)):
    if not body.post_ids:
        raise HTTPException(400, "No post IDs provided")
    deleted = 0
    for pid in body.post_ids:
        post = get_post(pid)
        if not post:
            continue
        file_path = post.get("file_path", "")
        log_audit(pid, user_email, "deleted", {"title": post.get("title", "")})
        delete_post(pid)
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        deleted += 1
    return {"ok": True, "deleted": deleted}


# ── AI Edit ───────────────────────────────────────────────────────────────────

class AiEditRequest(BaseModel):
    instruction: str
    selection: str | None = None


@app.post("/api/posts/{post_id}/ai-edit")
def api_ai_edit(post_id: int, body: AiEditRequest, _: str = Depends(require_auth)):
    """Apply a targeted AI edit to a post. If selection is provided, only that text is sent to Claude."""
    from src.ai_editor import ai_edit_selection, ai_edit_full

    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    if body.selection:
        edited = ai_edit_selection(body.selection, body.instruction)
        return {"edited_selection": edited}

    file_path = post.get("file_path", "")
    if file_path and os.path.exists(file_path):
        content = read_post_content(file_path)
    else:
        content = post.get("content", "")
        if not content:
            raise HTTPException(400, "Post file not found")

    try:
        edited = ai_edit_full(content, body.instruction)
    except Exception as e:
        raise HTTPException(500, f"AI edit failed: {e}")
    return {"edited_content": edited}


# ── Generate ──────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    keyword: str
    country: str | None = None
    aeo_prompt: str | None = None
    category: str | None = None
    max_tokens: int = 16000
    brand: str = "hitpay"


@app.post("/api/generate")
async def api_generate(body: GenerateRequest, user_email: str = Depends(require_auth)):
    """Generate a blog post and stream progress via SSE."""

    async def stream():
        messages: list[str] = []
        loop = asyncio.get_event_loop()

        def on_status(msg: str):
            messages.append(msg)

        try:
            yield f"data: {json.dumps({'type': 'status', 'message': 'Starting generation...'})}\n\n"

            post_data = await loop.run_in_executor(
                None, lambda: generate_blog_post(body.keyword, country=body.country, aeo_prompt=body.aeo_prompt, category=body.category, max_tokens=body.max_tokens, on_status=on_status, brand=body.brand)
            )

            for msg in messages:
                yield f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"

            # Handle slug collision
            existing = get_post_by_slug(post_data["slug"])
            if existing:
                import time
                post_data["slug"] = f"{post_data['slug']}-{int(time.time())}"

            post_data["editor_email"] = user_email
            file_path = write_post_file(post_data)
            post_id = save_post(post_data, file_path)
            post_data["id"] = post_id

            log_audit(post_id, user_email, "created", {
                "keyword": body.keyword,
                "country": body.country or "",
            })

            done_payload: dict = {"type": "done", "post_id": post_id, "title": post_data["title"]}
            if post_data.get("link_warnings"):
                done_payload["link_warnings"] = post_data["link_warnings"]
            yield f"data: {json.dumps(done_payload)}\n\n"

        except Exception as e:
            _err = str(e)
            _msg = "Claude API is busy right now — please try again in a few seconds" if "overloaded_error" in _err else _err
            yield f"data: {json.dumps({'type': 'error', 'message': _msg})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


class RewriteRequest(BaseModel):
    url: str
    country: str | None = None


@app.post("/api/rewrite")
async def api_rewrite(body: RewriteRequest, user_email: str = Depends(require_auth)):
    """Scrape an existing blog post URL and rewrite it with all optimisation directives. Streams via SSE."""

    async def stream():
        messages: list[str] = []
        loop = asyncio.get_event_loop()

        def on_status(msg: str):
            messages.append(msg)

        try:
            yield f"data: {json.dumps({'type': 'status', 'message': 'Starting rewrite...'})}\n\n"

            post_data = await loop.run_in_executor(
                None, lambda: rewrite_blog_post(body.url, country=body.country, on_status=on_status)
            )

            for msg in messages:
                yield f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"

            # Always use the slug from the original URL so the permalink doesn't change.
            from urllib.parse import urlparse
            url_slug = urlparse(body.url).path.rstrip("/").split("/")[-1]
            if url_slug:
                post_data["slug"] = url_slug

            post_data["editor_email"] = user_email
            file_path = write_post_file(post_data)
            post_id = save_post(post_data, file_path)
            post_data["id"] = post_id

            log_audit(post_id, user_email, "created", {
                "keyword": post_data.get("keyword", ""),
                "country": body.country or "",
                "source_url": body.url,
            })

            done_payload_rw: dict = {"type": "done", "post_id": post_id, "title": post_data["title"]}
            if post_data.get("link_warnings"):
                done_payload_rw["link_warnings"] = post_data["link_warnings"]
            yield f"data: {json.dumps(done_payload_rw)}\n\n"

        except Exception as e:
            _err = str(e)
            _msg = "Claude API is busy right now — please try again in a few seconds" if "overloaded_error" in _err else _err
            yield f"data: {json.dumps({'type': 'error', 'message': _msg})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


class FeedbackRequest(BaseModel):
    message: str


@app.post("/api/feedback")
async def api_submit_feedback(body: FeedbackRequest, user_email: str = Depends(require_auth)):
    if not body.message.strip():
        raise HTTPException(400, "Message cannot be empty")
    fid = save_feedback(user_email, body.message.strip())
    return {"id": fid}


@app.get("/api/analytics/status-durations")
def api_status_durations(period: str = "month", _: str = Depends(require_auth)):
    """Return average hours each post spent in each status, grouped by period.

    period: 'day' | 'week' | 'month'
    Returns rows: { period, status, avg_hours }
    """
    if period not in ("day", "week", "month"):
        period = "month"

    trunc   = {"day": "day",   "week": "week",   "month": "month"}[period]
    fmt     = {"day": "YYYY-MM-DD", "week": "YYYY-MM-DD", "month": "YYYY-MM"}[period]
    n_back  = {"day": 14, "week": 12, "month": 12}[period]

    from src.database import get_connection, _rows_to_dicts
    conn = get_connection()
    rows = conn.run(f"""
        WITH transitions AS (
            SELECT
                post_id,
                details::json->>'from'  AS from_status,
                timestamp               AS transitioned_at,
                LAG(timestamp) OVER (PARTITION BY post_id ORDER BY timestamp) AS prev_ts
            FROM audit_log
            WHERE action = 'status_changed'
        ),
        durations AS (
            SELECT
                from_status                                              AS status,
                EXTRACT(EPOCH FROM (transitioned_at - prev_ts)) / 3600  AS hours,
                DATE_TRUNC('{trunc}', transitioned_at)                   AS period
            FROM transitions
            WHERE prev_ts IS NOT NULL AND from_status IS NOT NULL
              AND EXTRACT(EPOCH FROM (transitioned_at - prev_ts)) > 0
              AND transitioned_at >= NOW() - INTERVAL '{n_back} {trunc}s'
        )
        SELECT
            TO_CHAR(period, '{fmt}')       AS period,
            status,
            ROUND(AVG(hours)::numeric, 2)  AS avg_hours
        FROM durations
        GROUP BY period, status
        ORDER BY period, status
    """)
    return _rows_to_dicts(conn, rows)


@app.get("/api/analytics/blog")
async def api_blog_analytics(
    request: Request,
    days: int = 7,
    start: str = None,
    end: str = None,
    _: str = Depends(require_auth),
):
    """Bulk GA4 sessions/users per post slug, for the overview table."""
    from src.ga4_analytics import fetch_blog_analytics
    access_token = await get_ga_access_token(request)
    return fetch_blog_analytics(access_token, days, start, end)


@app.get("/api/posts/{post_id}/analytics")
async def api_post_analytics(
    post_id: int,
    request: Request,
    days: int = 7,
    start: str = None,
    end: str = None,
    _: str = Depends(require_auth),
):
    """GA4 metrics for a single post's slug, for the post detail view."""
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    from src.ga4_analytics import fetch_blog_analytics
    access_token = await get_ga_access_token(request)
    result = fetch_blog_analytics(access_token, days, start, end)
    metrics = result.get("data", {}).get(post["slug"], {})
    return {
        "configured": result.get("configured", False),
        "error": result.get("error"),
        "as_of": result.get("as_of"),
        "metrics": metrics,
    }


@app.get("/api/feedback")
def api_list_feedback(_: str = Depends(require_auth)):
    return list_feedback()


@app.get("/api/logins")
def api_list_logins(_: str = Depends(require_auth)):
    return list_logins()


@app.post("/api/test-post")
async def api_test_post(user_email: str = Depends(require_auth)):
    """Create a placeholder test post instantly (no AI generation)."""
    import time
    ts = int(time.time())
    post_data = {
        "title": f"Test Post {ts}",
        "slug": f"test-post-{ts}",
        "keyword": "[TEST]",
        "country": "",
        "status": "generated",
        "date": __import__("datetime").date.today().isoformat(),
        "meta_title": "",
        "meta_description": "",
        "overview": "This is a placeholder test post.",
        "categories": [],
        "tags": [],
        "content": "This is a test post created for prototype testing purposes.\n\nReplace this content with your actual post body.",
    }
    post_data["editor_email"] = user_email
    file_path = write_post_file(post_data)
    post_id = save_post(post_data, file_path)
    log_audit(post_id, user_email, "created", {"keyword": "[TEST]", "country": ""})
    return {"post_id": post_id}


# ── X Posts ───────────────────────────────────────────────────────────────────

from src.x_database import (
    list_x_posts,
    get_x_post,
    get_x_posts_by_blog_post_id,
    create_x_post,
    update_x_post,
    change_x_post_status as _change_x_status,
    delete_x_post,
    log_x_audit,
    get_x_audit_log,
)


class CreateXPostRequest(BaseModel):
    content: str
    market: str | None = None
    scheduled_at: str | None = None
    brand: str = "hitpay"


class UpdateXPostRequest(BaseModel):
    content: str | None = None
    market: str | None = None
    scheduled_at: str | None = None


class XStatusRequest(BaseModel):
    status: str
    scheduled_at: str | None = None
    post_url: str | None = None


@app.get("/api/x-posts")
def api_list_x_posts(status: str = None, market: str = None, brand: str = None, _: str = Depends(require_auth)):
    posts = list_x_posts(
        status if status and status != "all" else None,
        market if market and market != "all" else None,
        brand if brand else None,
    )
    return posts


@app.post("/api/x-posts")
def api_create_x_post(body: CreateXPostRequest, user_email: str = Depends(require_auth)):
    if not body.content.strip():
        raise HTTPException(400, "Content cannot be empty")
    post_id = create_x_post(
        content=body.content.strip(),
        market=body.market or None,
        scheduled_at=body.scheduled_at or None,
        editor_email=user_email,
        brand=body.brand or "hitpay",
    )
    log_x_audit(post_id, user_email, "created", {"market": body.market or ""})
    return {"id": post_id}


@app.get("/api/x-posts/{post_id}")
def api_get_x_post(post_id: int, _: str = Depends(require_auth)):
    post = get_x_post(post_id)
    if not post:
        raise HTTPException(404, "X post not found")
    return post


@app.put("/api/x-posts/{post_id}")
def api_update_x_post(post_id: int, body: UpdateXPostRequest, user_email: str = Depends(require_auth)):
    post = get_x_post(post_id)
    if not post:
        raise HTTPException(404, "X post not found")
    fields = {}
    changed = []
    if body.content is not None:
        fields["content"] = body.content.strip()
        changed.append("content")
    if body.market is not None:
        fields["market"] = body.market or None
        changed.append("market")
    if body.scheduled_at is not None:
        fields["scheduled_at"] = body.scheduled_at or None
        changed.append("scheduled_at")
    if fields:
        update_x_post(post_id, fields)
        log_x_audit(post_id, user_email, "edited", {"fields": changed})
    return {"ok": True}


@app.post("/api/x-posts/{post_id}/status")
def api_change_x_post_status(post_id: int, body: XStatusRequest, user_email: str = Depends(require_auth)):
    valid = ["draft", "scheduled", "posted"]
    if body.status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {valid}")
    post = get_x_post(post_id)
    if not post:
        raise HTTPException(404, "X post not found")
    _change_x_status(post_id, body.status, scheduled_at=body.scheduled_at, post_url=body.post_url)
    log_x_audit(post_id, user_email, "status_changed", {"from": post.get("status"), "to": body.status})
    return {"ok": True}


@app.delete("/api/x-posts/{post_id}")
def api_delete_x_post(post_id: int, user_email: str = Depends(require_auth)):
    post = get_x_post(post_id)
    if not post:
        raise HTTPException(404, "X post not found")
    log_x_audit(post_id, user_email, "deleted", {"content_preview": (post.get("content") or "")[:60]})
    delete_x_post(post_id)
    return {"ok": True}


class XBulkDeleteRequest(BaseModel):
    ids: list[int]


@app.post("/api/x-posts/bulk-delete")
def api_bulk_delete_x_posts(body: XBulkDeleteRequest, user_email: str = Depends(require_auth)):
    deleted = []
    for pid in body.ids:
        post = get_x_post(pid)
        if post:
            log_x_audit(pid, user_email, "deleted", {"content_preview": (post.get("content") or "")[:60]})
            delete_x_post(pid)
            deleted.append(pid)
    return {"deleted": deleted}


@app.get("/api/x-posts/{post_id}/audit-log")
def api_get_x_audit_log(post_id: int, _: str = Depends(require_auth)):
    return get_x_audit_log(post_id)


def _check_link_url(content: str) -> str | None:
    """Return a warning string if any https://hitpayapp.com/blog/* URL in content is a 404."""
    import re as _re
    urls = _re.findall(r"https://hitpayapp\.com/blog/[^\s\)\"']+", content)
    for url in urls:
        try:
            r = httpx.head(url, follow_redirects=True, timeout=6)
            if r.status_code == 404:
                return f"Link returns 404: {url}"
        except Exception:
            pass
    return None


class UpdateAndSyncXRequest(BaseModel):
    content: str
    market: str | None = None
    scheduled_at: str | None = None


@app.post("/api/x-posts/{post_id}/update-and-sync")
def api_update_and_sync_x_post(post_id: int, body: UpdateAndSyncXRequest,
                                user_email: str = Depends(require_auth)):
    """Save edits to DB."""
    post = get_x_post(post_id)
    if not post:
        raise HTTPException(404, "X post not found")

    # Persist the edited content
    fields: dict = {"content": body.content.strip()}
    if body.market is not None:
        fields["market"] = body.market or None
    if body.scheduled_at is not None:
        fields["scheduled_at"] = body.scheduled_at or None
    update_x_post(post_id, fields)
    log_x_audit(post_id, user_email, "edited", {"fields": list(fields.keys()), "source": "update_and_sync"})

    return {"ok": True}


class GenerateThoughtLeadershipRequest(BaseModel):
    market: str | None = None
    topic_hint: str | None = None
    thread_size: int = 7  # 1, 2, 3, 5, or 7
    style: str = "educational"  # "educational" or "storytelling"
    brand: str = "hitpay"
    content_type: str | None = None  # if set, routes to generate_random_x_post with fixed format


@app.post("/api/x-posts/generate-thought-leadership")
def api_generate_thought_leadership(
    body: GenerateThoughtLeadershipRequest,
    _: str = Depends(require_auth),
):
    from src.thought_leadership import generate_thought_leadership_thread, generate_random_x_post, CONTENT_TYPE_CONFIGS
    try:
        if body.content_type and body.content_type in CONTENT_TYPE_CONFIGS:
            result = generate_random_x_post(
                market=body.market or None,
                topic_hint=body.topic_hint or None,
                brand=body.brand,
                content_type=body.content_type,
            )
        else:
            result = generate_thought_leadership_thread(
                market=body.market or None,
                topic_hint=body.topic_hint or None,
                thread_size=body.thread_size,
                style=body.style,
                brand=body.brand,
            )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")
    return result


@app.post("/api/x-posts/generate-random")
def api_generate_random_x_post(
    market: str | None = None,
    topic_hint: str | None = None,
    brand: str = "hitpay",
    _: str = Depends(require_auth),
):
    """Generate a post with randomized style + thread_size — entry point for automated scheduling."""
    from src.thought_leadership import generate_random_x_post
    try:
        result = generate_random_x_post(market=market or None, topic_hint=topic_hint or None, brand=brand)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")
    return result


class GenerateChangelogXRequest(BaseModel):
    market: str | None = None
    limit: int = 10
    brand: str = "hitpay"


@app.post("/api/x-posts/generate-from-changelog")
def api_generate_x_from_changelog(
    body: GenerateChangelogXRequest,
    user_email: str = Depends(require_auth),
):
    from src.changelog_social import generate_x_from_changelog
    try:
        result = generate_x_from_changelog(market=body.market, limit=body.limit, brand=body.brand)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")

    created = []

    roundup = result["roundup"]
    link = roundup.get("link_url") or ""
    roundup_tweets = roundup.get("tweets", [])
    roundup_content = "\n\n---\n\n".join(_cap_tweet(t.replace("[URL]", link)) for t in roundup_tweets)
    roundup_id = create_x_post(content=roundup_content, market=body.market, editor_email=user_email, brand=body.brand)
    log_x_audit(roundup_id, user_email, "created", {"source": "changelog_roundup"})
    created.append({"id": roundup_id, "type": "roundup", "preview": roundup_tweets[0][:100] if roundup_tweets else ""})

    for item in result["individual"]:
        tweet = item.get("tweet", "")
        item_link = item.get("link_url") or ""
        content = _cap_tweet(tweet.replace("[URL]", item_link))
        post_id = create_x_post(content=content, market=body.market, editor_email=user_email, brand=body.brand)
        log_x_audit(post_id, user_email, "created", {"source": "changelog_individual", "title": item.get("title", "")})
        created.append({"id": post_id, "type": "individual", "title": item.get("title", ""), "preview": content[:100]})

    return {"created": created, "total": len(created), "market": body.market}


# ── Threads Posts ─────────────────────────────────────────────────────────────

from src.threads_database import (
    list_threads_posts,
    get_threads_post,
    get_threads_posts_by_blog_post_id,
    create_threads_post,
    update_threads_post,
    change_threads_post_status as _change_thr_status,
    delete_threads_post,
    log_threads_audit,
    get_threads_audit_log,
)


class CreateThreadsPostRequest(BaseModel):
    content: str
    market: str | None = None
    scheduled_at: str | None = None
    brand: str = "hitpay"


class UpdateThreadsPostRequest(BaseModel):
    content: str | None = None
    market: str | None = None
    scheduled_at: str | None = None


class ThreadsStatusRequest(BaseModel):
    status: str
    scheduled_at: str | None = None
    post_url: str | None = None


class ThreadsBulkDeleteRequest(BaseModel):
    ids: list[int]


class GenerateThreadsStoryRequest(BaseModel):
    market: str | None = None
    topic_hint: str | None = None
    thread_size: int = 3  # 1, 3, or 5
    brand: str = "hitpay"


@app.get("/api/threads-posts")
def api_list_threads_posts(status: str = None, market: str = None, brand: str = None, _: str = Depends(require_auth)):
    return list_threads_posts(
        status if status and status != "all" else None,
        market if market and market != "all" else None,
        brand if brand else None,
    )


@app.post("/api/threads-posts")
def api_create_threads_post(body: CreateThreadsPostRequest, user_email: str = Depends(require_auth)):
    if not body.content.strip():
        raise HTTPException(400, "Content cannot be empty")
    post_id = create_threads_post(
        content=body.content.strip(),
        market=body.market or None,
        scheduled_at=body.scheduled_at or None,
        editor_email=user_email,
        brand=body.brand or "hitpay",
    )
    log_threads_audit(post_id, user_email, "created", {"market": body.market or ""})
    return {"id": post_id}


@app.get("/api/threads-posts/{post_id}")
def api_get_threads_post(post_id: int, _: str = Depends(require_auth)):
    post = get_threads_post(post_id)
    if not post:
        raise HTTPException(404, "Threads post not found")
    return post


@app.put("/api/threads-posts/{post_id}")
def api_update_threads_post(post_id: int, body: UpdateThreadsPostRequest, user_email: str = Depends(require_auth)):
    post = get_threads_post(post_id)
    if not post:
        raise HTTPException(404, "Threads post not found")
    fields, changed = {}, []
    if body.content is not None:
        fields["content"] = body.content.strip()
        changed.append("content")
    if body.market is not None:
        fields["market"] = body.market or None
        changed.append("market")
    if body.scheduled_at is not None:
        fields["scheduled_at"] = body.scheduled_at or None
        changed.append("scheduled_at")
    if fields:
        update_threads_post(post_id, fields)
        log_threads_audit(post_id, user_email, "edited", {"fields": changed})
    return {"ok": True}


@app.post("/api/threads-posts/{post_id}/status")
def api_change_threads_post_status(post_id: int, body: ThreadsStatusRequest, user_email: str = Depends(require_auth)):
    valid = ["draft", "scheduled", "posted"]
    if body.status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {valid}")
    post = get_threads_post(post_id)
    if not post:
        raise HTTPException(404, "Threads post not found")
    _change_thr_status(post_id, body.status, scheduled_at=body.scheduled_at, post_url=body.post_url)
    log_threads_audit(post_id, user_email, "status_changed", {"from": post.get("status"), "to": body.status})
    return {"ok": True}


@app.delete("/api/threads-posts/{post_id}")
def api_delete_threads_post(post_id: int, user_email: str = Depends(require_auth)):
    post = get_threads_post(post_id)
    if not post:
        raise HTTPException(404, "Threads post not found")
    log_threads_audit(post_id, user_email, "deleted", {"content_preview": (post.get("content") or "")[:60]})
    delete_threads_post(post_id)
    return {"ok": True}


@app.post("/api/threads-posts/bulk-delete")
def api_bulk_delete_threads_posts(body: ThreadsBulkDeleteRequest, user_email: str = Depends(require_auth)):
    deleted = []
    for pid in body.ids:
        post = get_threads_post(pid)
        if post:
            log_threads_audit(pid, user_email, "deleted", {"content_preview": (post.get("content") or "")[:60]})
            delete_threads_post(pid)
            deleted.append(pid)
    return {"deleted": deleted}


@app.get("/api/threads-posts/{post_id}/audit-log")
def api_get_threads_audit_log(post_id: int, _: str = Depends(require_auth)):
    return get_threads_audit_log(post_id)


@app.post("/api/threads-posts/generate-story")
def api_generate_threads_story(body: GenerateThreadsStoryRequest, _: str = Depends(require_auth)):
    from src.threads_thought_leadership import generate_threads_story
    try:
        result = generate_threads_story(
            market=body.market or None,
            topic_hint=body.topic_hint or None,
            thread_size=body.thread_size,
            brand=body.brand,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")
    return result


class GenerateChangelogThreadsRequest(BaseModel):
    market: str | None = None
    limit: int = 10
    brand: str = "hitpay"


@app.post("/api/threads-posts/generate-from-changelog")
def api_generate_threads_from_changelog(
    body: GenerateChangelogThreadsRequest,
    user_email: str = Depends(require_auth),
):
    from src.changelog_social import generate_threads_from_changelog
    try:
        result = generate_threads_from_changelog(market=body.market, limit=body.limit, brand=body.brand)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")

    created = []

    roundup = result["roundup"]
    roundup_posts = roundup.get("posts", [])
    roundup_link = roundup.get("link_url") or ""
    roundup_content = "\n\n---\n\n".join(p.replace("[URL]", roundup_link) for p in roundup_posts)
    roundup_id = create_threads_post(content=roundup_content, market=body.market, editor_email=user_email, brand=body.brand)
    log_threads_audit(roundup_id, user_email, "created", {"source": "changelog_roundup"})
    created.append({"id": roundup_id, "type": "roundup", "preview": roundup_posts[0][:100] if roundup_posts else ""})

    for item in result["individual"]:
        post_text = item.get("post", "")
        item_link = item.get("link_url") or ""
        content = post_text.replace("[URL]", item_link)
        post_id = create_threads_post(content=content, market=body.market, editor_email=user_email, brand=body.brand)
        log_threads_audit(post_id, user_email, "created", {"source": "changelog_individual", "title": item.get("title", "")})
        created.append({"id": post_id, "type": "individual", "title": item.get("title", ""), "preview": content[:100]})

    return {"created": created, "total": len(created), "market": body.market}


# ── LinkedIn Posts ────────────────────────────────────────────────────────────

from src.linkedin_database import (
    list_linkedin_posts,
    get_linkedin_post,
    get_linkedin_posts_by_blog_post_id,
    create_linkedin_post,
    update_linkedin_post,
    change_linkedin_post_status as _change_li_status,
    delete_linkedin_post,
    log_linkedin_audit,
    get_linkedin_audit_log,
)


class CreateLinkedInPostRequest(BaseModel):
    content: str
    market: str | None = None
    scheduled_at: str | None = None
    brand: str = "hitpay"


class UpdateLinkedInPostRequest(BaseModel):
    content: str | None = None
    market: str | None = None
    scheduled_at: str | None = None


class LinkedInStatusRequest(BaseModel):
    status: str
    scheduled_at: str | None = None
    post_url: str | None = None


class LinkedInBulkDeleteRequest(BaseModel):
    ids: list[int]


class GenerateLinkedInPostRequest(BaseModel):
    market: str | None = None
    topic_hint: str | None = None
    brand: str = "hitpay"


class GenerateChangelogLinkedInRequest(BaseModel):
    market: str | None = None
    limit: int = 10
    brand: str = "hitpay"


@app.get("/api/linkedin-posts")
def api_list_linkedin_posts(status: str = None, market: str = None, brand: str = None, _: str = Depends(require_auth)):
    return list_linkedin_posts(
        status if status and status != "all" else None,
        market if market and market != "all" else None,
        brand if brand else None,
    )


@app.post("/api/linkedin-posts")
def api_create_linkedin_post(body: CreateLinkedInPostRequest, user_email: str = Depends(require_auth)):
    if not body.content.strip():
        raise HTTPException(400, "Content cannot be empty")
    post_id = create_linkedin_post(
        content=body.content.strip(),
        market=body.market or None,
        scheduled_at=body.scheduled_at or None,
        editor_email=user_email,
        brand=body.brand or "hitpay",
    )
    log_linkedin_audit(post_id, user_email, "created", {"market": body.market or ""})
    return {"id": post_id}


@app.get("/api/linkedin-posts/{post_id}")
def api_get_linkedin_post(post_id: int, _: str = Depends(require_auth)):
    post = get_linkedin_post(post_id)
    if not post:
        raise HTTPException(404, "LinkedIn post not found")
    return post


@app.put("/api/linkedin-posts/{post_id}")
def api_update_linkedin_post(post_id: int, body: UpdateLinkedInPostRequest, user_email: str = Depends(require_auth)):
    post = get_linkedin_post(post_id)
    if not post:
        raise HTTPException(404, "LinkedIn post not found")
    fields, changed = {}, []
    if body.content is not None:
        fields["content"] = body.content.strip()
        changed.append("content")
    if body.market is not None:
        fields["market"] = body.market or None
        changed.append("market")
    if body.scheduled_at is not None:
        fields["scheduled_at"] = body.scheduled_at or None
        changed.append("scheduled_at")
    if fields:
        update_linkedin_post(post_id, fields)
        log_linkedin_audit(post_id, user_email, "edited", {"fields": changed})
    return {"ok": True}


@app.post("/api/linkedin-posts/{post_id}/status")
def api_change_linkedin_post_status(post_id: int, body: LinkedInStatusRequest, user_email: str = Depends(require_auth)):
    valid = ["draft", "scheduled", "posted"]
    if body.status not in valid:
        raise HTTPException(400, f"Invalid status. Must be one of: {valid}")
    post = get_linkedin_post(post_id)
    if not post:
        raise HTTPException(404, "LinkedIn post not found")
    _change_li_status(post_id, body.status, scheduled_at=body.scheduled_at, post_url=body.post_url)
    log_linkedin_audit(post_id, user_email, "status_changed", {"from": post.get("status"), "to": body.status})
    return {"ok": True}


@app.delete("/api/linkedin-posts/{post_id}")
def api_delete_linkedin_post(post_id: int, user_email: str = Depends(require_auth)):
    post = get_linkedin_post(post_id)
    if not post:
        raise HTTPException(404, "LinkedIn post not found")
    log_linkedin_audit(post_id, user_email, "deleted", {"content_preview": (post.get("content") or "")[:60]})
    delete_linkedin_post(post_id)
    return {"ok": True}


@app.post("/api/linkedin-posts/bulk-delete")
def api_bulk_delete_linkedin_posts(body: LinkedInBulkDeleteRequest, user_email: str = Depends(require_auth)):
    deleted = []
    for pid in body.ids:
        post = get_linkedin_post(pid)
        if post:
            log_linkedin_audit(pid, user_email, "deleted", {"content_preview": (post.get("content") or "")[:60]})
            delete_linkedin_post(pid)
            deleted.append(pid)
    return {"deleted": deleted}


@app.get("/api/linkedin-posts/{post_id}/audit-log")
def api_get_linkedin_audit_log(post_id: int, _: str = Depends(require_auth)):
    return get_linkedin_audit_log(post_id)


@app.post("/api/linkedin-posts/generate-thought-leadership")
def api_generate_linkedin_post(body: GenerateLinkedInPostRequest, _: str = Depends(require_auth)):
    from src.linkedin_generator import generate_linkedin_post
    try:
        result = generate_linkedin_post(
            market=body.market or None,
            topic_hint=body.topic_hint or None,
            brand=body.brand,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")
    return result


@app.post("/api/linkedin-posts/generate-from-changelog")
def api_generate_linkedin_from_changelog(
    body: GenerateChangelogLinkedInRequest,
    user_email: str = Depends(require_auth),
):
    from src.mcp_client import get_changelog
    from src.changelog_social import _extract_changelog_text
    from src.linkedin_generator import generate_linkedin_from_changelog
    try:
        mcp_result = get_changelog(limit=body.limit)
        changelog_text = _extract_changelog_text(mcp_result)
        if not changelog_text.strip():
            raise HTTPException(422, "No changelog entries found — check HITPAY_MCP_URL is configured.")
        result = generate_linkedin_from_changelog(changelog_text=changelog_text, brand=body.brand)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")

    content = result.get("content", "")
    link_url = result.get("link_url", "")
    full_content = content.replace("[URL]", link_url) if "[URL]" in content else content

    post_id = create_linkedin_post(content=full_content, market=body.market, editor_email=user_email, brand=body.brand)
    log_linkedin_audit(post_id, user_email, "created", {"source": "changelog"})
    return {"created": [{"id": post_id, "preview": full_content[:100]}], "total": 1, "market": body.market}


# ── Repurpose for Social ─────────────────────────────────────────────────────

from src.repurposer import repurpose_for_platform, _cap_tweet, _cap_tweet_post_url, _move_url_to_reply, repurpose_post_as_thread, repurpose_edm


class RepurposeRequest(BaseModel):
    platform: str = "twitter"
    brand: str | None = None  # if None, inherits from the post's brand field


class RepurposeToXRequest(BaseModel):
    format_key: str
    blog_url: str
    tweets: list[str] | None = None
    link_reply: str | None = None
    market: str | None = None


class RepurposeCardRequest(BaseModel):
    card_type: str   # "quick_hit" | "thread" | "contextual" | "market"
    hook_style: str  # "Curiosity" | "Contrarian" | "Result" | "Mistake" | "List"


class RepurposeEDMRequest(BaseModel):
    edm_content: str
    market: str | None = None


@app.get("/api/config")
def api_config(_: str = Depends(require_auth)):
    return {}


@app.post("/api/posts/{post_id}/repurpose")
async def api_repurpose(post_id: int, body: RepurposeRequest,
                        user_email: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    async def stream():
        messages: list[str] = []
        loop = asyncio.get_event_loop()

        def on_status(msg: str):
            messages.append(msg)

        try:
            yield f"data: {json.dumps({'type': 'status', 'message': 'Generating social content...'})}\n\n"
            result = await loop.run_in_executor(
                None, lambda: repurpose_for_platform(post, body.platform, on_status=on_status)
            )
            for msg in messages:
                yield f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"
            update_repurposed_content(post_id, body.platform, result)
            log_audit(post_id, user_email, "repurposed", {"platform": body.platform})
            yield f"data: {json.dumps({'type': 'done', 'result': result})}\n\n"
        except Exception as e:
            _err = str(e)
            _msg = "Claude API is busy right now — please try again in a few seconds" if "overloaded_error" in _err else _err
            yield f"data: {json.dumps({'type': 'error', 'message': _msg})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/posts/{post_id}/repurpose-card")
async def api_repurpose_card(post_id: int, body: RepurposeCardRequest,
                              user_email: str = Depends(require_auth)):
    valid_types = {"quick_hit", "thread", "contextual", "market"}
    valid_styles = {"Curiosity", "Contrarian", "Result", "Mistake", "List"}
    if body.card_type not in valid_types:
        raise HTTPException(400, f"card_type must be one of: {sorted(valid_types)}")
    if body.hook_style not in valid_styles:
        raise HTTPException(400, f"hook_style must be one of: {sorted(valid_styles)}")

    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    from src.repurposer import _generate_twitter_card

    async def stream():
        messages: list[str] = []
        loop = asyncio.get_event_loop()

        def on_status(msg: str):
            messages.append(msg)

        try:
            yield f"data: {json.dumps({'type': 'status', 'message': f'Regenerating {body.card_type} card...'})}\n\n"
            card = await loop.run_in_executor(
                None,
                lambda: _generate_twitter_card(post, body.card_type, body.hook_style, on_status=on_status),
            )
            for msg in messages:
                yield f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"
            # Persist: replace the matching card in stored data
            repurposed = get_repurposed_content(post_id) or {}
            twitter_data = repurposed.get("twitter", {}) if isinstance(repurposed.get("twitter"), dict) else repurposed
            choices = twitter_data.get("choices", []) if isinstance(twitter_data, dict) else []
            updated = False
            for i, c in enumerate(choices):
                if isinstance(c, dict) and c.get("type") == body.card_type:
                    choices[i] = card
                    updated = True
                    break
            if not updated:
                choices.append(card)
            if isinstance(twitter_data, dict):
                twitter_data["choices"] = choices
            update_repurposed_content(post_id, "twitter", twitter_data)
            log_audit(post_id, user_email, "repurposed_card", {
                "card_type": body.card_type, "hook_style": body.hook_style
            })
            yield f"data: {json.dumps({'type': 'done', 'card': card})}\n\n"
        except Exception as e:
            _err = str(e)
            _msg = "Claude API is busy right now — please try again in a few seconds" if "overloaded_error" in _err else _err
            yield f"data: {json.dumps({'type': 'error', 'message': _msg})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


class RepurposeThreadRequest(BaseModel):
    thread_size: int = 7  # 1, 3, 5, or 7


@app.post("/api/posts/{post_id}/repurpose-thread")
async def api_repurpose_thread(post_id: int, body: RepurposeThreadRequest,
                                user_email: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    async def stream():
        loop = asyncio.get_event_loop()
        try:
            label = "tweet" if body.thread_size == 1 else f"{body.thread_size}-tweet thread"
            yield f"data: {json.dumps({'type': 'status', 'message': f'Generating {label}…'})}\n\n"
            result = await loop.run_in_executor(
                None, lambda: repurpose_post_as_thread(post, body.thread_size)
            )
            usage = result.pop("usage", None)
            update_repurposed_content(post_id, "twitter", result)
            log_audit(post_id, user_email, "repurposed", {"platform": "twitter", "thread_size": body.thread_size})
            yield f"data: {json.dumps({'type': 'done', 'result': result, 'usage': usage})}\n\n"
        except Exception as e:
            _err = str(e)
            _msg = "Claude API is busy right now — please try again in a few seconds" if "overloaded_error" in _err else _err
            yield f"data: {json.dumps({'type': 'error', 'message': _msg})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


class BulkRepurposeRequest(BaseModel):
    post_ids: list[int]
    platform: str = "x"        # "x" or "threads"
    thread_size: int | None = None  # None = random per post


@app.post("/api/posts/bulk-repurpose")
async def api_bulk_repurpose(body: BulkRepurposeRequest,
                              user_email: str = Depends(require_auth)):
    sizes = [1, 3, 5, 7]

    async def stream():
        loop = asyncio.get_event_loop()
        total = len(body.post_ids)
        for i, post_id in enumerate(body.post_ids):
            size = body.thread_size if body.thread_size else random.choice(sizes)
            post = get_post(post_id)
            if not post:
                yield f"data: {json.dumps({'type': 'skip', 'post_id': post_id, 'reason': 'not found'})}\n\n"
                continue
            try:
                yield f"data: {json.dumps({'type': 'progress', 'index': i, 'total': total, 'post_id': post_id, 'title': post.get('title', '')})}\n\n"
                _size = size
                result = await loop.run_in_executor(
                    None, lambda: repurpose_post_as_thread(post, _size)
                )
                result.pop("usage", None)
                update_repurposed_content(post_id, "twitter", result)
                log_audit(post_id, user_email, "repurposed", {
                    "platform": body.platform, "thread_size": _size, "bulk": True
                })
                yield f"data: {json.dumps({'type': 'done_one', 'post_id': post_id, 'thread_size': _size})}\n\n"
            except Exception as e:
                _err = str(e)
                yield f"data: {json.dumps({'type': 'error_one', 'post_id': post_id, 'message': _err})}\n\n"
        yield f"data: {json.dumps({'type': 'done_all', 'total': total})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/repurpose/edm")
async def api_repurpose_edm(body: RepurposeEDMRequest, user_email: str = Depends(require_auth)):
    async def stream():
        loop = asyncio.get_event_loop()
        try:
            yield f"data: {json.dumps({'type': 'status', 'message': 'Generating X posts…'})}\n\n"
            result = await loop.run_in_executor(
                None, lambda: repurpose_edm(body.edm_content, body.market)
            )
            usage = result.pop("usage", None)
            yield f"data: {json.dumps({'type': 'done', 'result': result, 'usage': usage})}\n\n"
        except Exception as e:
            _err = str(e)
            _msg = "Claude API is busy right now — please try again in a few seconds" if "overloaded_error" in _err else _err
            yield f"data: {json.dumps({'type': 'error', 'message': _msg})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/posts/{post_id}/repurposed")
def api_get_repurposed(post_id: int, _: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")
    data = get_repurposed_content(post_id)
    return data or {}


@app.put("/api/posts/{post_id}/repurposed")
async def api_save_repurposed(post_id: int, request: Request,
                               user_email: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")
    data = await request.json()
    update_repurposed_content(post_id, "twitter", data)
    return {"ok": True}


@app.post("/api/posts/{post_id}/repurpose-to-x-drafts")
def api_repurpose_to_x_drafts(post_id: int, body: RepurposeToXRequest,
                               user_email: str = Depends(require_auth)):
    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    repurposed = get_repurposed_content(post_id)
    twitter_data = (repurposed or {}).get("twitter", {})

    # Resolve tweets and link_reply from override or stored data
    tweets: list[str] = []
    link_reply: str = ""

    if body.tweets is not None:
        tweets = body.tweets
        link_reply = body.link_reply or ""
    else:
        fk = body.format_key
        if fk == "stat_hook":
            d = twitter_data.get("stat_hook") or {}
            tweets = [d.get("tweet", "")]
            link_reply = d.get("link_reply", "")
        elif fk == "quick_answer_thread":
            d = twitter_data.get("quick_answer_thread") or {}
            tweets = d.get("tweets", [])
            link_reply = d.get("link_reply", "")
        elif fk == "comparison_tweet":
            d = twitter_data.get("comparison_tweet") or {}
            tweets = [d.get("tweet", "")]
            link_reply = d.get("link_reply", "")
        elif fk == "howto_thread":
            d = twitter_data.get("howto_thread") or {}
            tweets = d.get("tweets", [])
            link_reply = d.get("link_reply", "")
        elif fk in ("market_sg", "market_my", "market_ph"):
            mkt = fk.split("_")[1].upper()
            d = (twitter_data.get("market_tweets") or {}).get(mkt) or {}
            tweets = [d.get("tweet", "")]
            link_reply = d.get("link_reply", "")

    if not tweets:
        raise HTTPException(400, "No tweet content found for this format")

    blog_url = body.blog_url
    market = body.market or post.get("country") or None

    THREAD_SEP = "\n\n---\n\n"
    all_parts = [t.replace("[URL]", blog_url) for t in tweets]
    if link_reply:
        all_parts.append(link_reply.replace("[URL]", blog_url))
    content = THREAD_SEP.join(all_parts)

    xid = create_x_post(
        content=content,
        market=market,
        editor_email=user_email,
        source_blog_post_id=post_id,
        brand=post.get("brand", "hitpay"),
    )
    log_x_audit(xid, user_email, "created", {
        "source": f"repurpose:{body.format_key}",
        "tweet_count": len(all_parts),
    })
    log_audit(post_id, user_email, "added_to_x_drafts", {"format": body.format_key, "count": 1})
    return {"ok": True, "created_ids": [xid]}


@app.get("/api/posts/{post_id}/social-posts")
def api_get_social_posts(post_id: int, user_email: str = Depends(require_auth)):
    """Return all X, Threads, and LinkedIn drafts linked to a blog post."""
    def safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            return []
    return {
        "x": safe(get_x_posts_by_blog_post_id, post_id),
        "threads": safe(get_threads_posts_by_blog_post_id, post_id),
        "linkedin": safe(get_linkedin_posts_by_blog_post_id, post_id),
    }


@app.post("/api/posts/{post_id}/repurpose-all")
def api_repurpose_all(post_id: int, user_email: str = Depends(require_auth)):
    """Generate X, Threads, and LinkedIn drafts from a blog post in parallel."""
    import concurrent.futures
    from src.threads_thought_leadership import generate_threads_story
    from src.linkedin_generator import generate_linkedin_post as _gen_li

    post = get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")

    market = (post.get("country") or "SG") or "SG"
    brand = post.get("brand", "hitpay")
    slug = (post.get("slug") or "").strip()
    topic_hint = (post.get("title") or "")[:150]

    THREAD_SEP = "\n\n---\n\n"

    def gen_x():
        thread_size = random.choice([1, 3, 5])
        result = repurpose_post_as_thread(post, thread_size)
        blog_url = result.get("link_url", "")
        tweets = [_cap_tweet_post_url(t.replace("[URL]", blog_url)) for t in (result.get("tweets") or [])]
        content = THREAD_SEP.join(tweets)
        xid = create_x_post(
            content=content,
            market=market,
            editor_email=user_email,
            source_blog_post_id=post_id,
            brand=brand,
        )
        log_x_audit(xid, user_email, "created", {"source": "repurpose-all"})
        return xid

    def gen_threads():
        result = generate_threads_story(market=market, topic_hint=topic_hint, thread_size=3, brand=brand)
        posts = result.get("posts") or []
        content = THREAD_SEP.join(posts) if len(posts) > 1 else (posts[0] if posts else "")
        tid = create_threads_post(
            content=content,
            market=market,
            editor_email=user_email,
            source_blog_post_id=post_id,
            brand=brand,
        )
        log_threads_audit(tid, user_email, "created", {"source": "repurpose-all"})
        return tid

    def gen_linkedin():
        result = _gen_li(market=market, topic_hint=topic_hint, brand=brand)
        content = result.get("content", "")
        lid = create_linkedin_post(
            content=content,
            market=market,
            editor_email=user_email,
            source_blog_post_id=post_id,
            brand=brand,
        )
        log_linkedin_audit(lid, user_email, "created", {"source": "repurpose-all"})
        return lid

    errors = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            "x": executor.submit(gen_x),
            "threads": executor.submit(gen_threads),
            "linkedin": executor.submit(gen_linkedin),
        }
        results = {}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception as exc:
                errors[key] = str(exc)
                results[key] = None

    log_audit(post_id, user_email, "repurposed_all", {
        "x_id": results.get("x"),
        "threads_id": results.get("threads"),
        "linkedin_id": results.get("linkedin"),
        "errors": errors or None,
    })

    if errors:
        return {
            "ok": False,
            "x_id": results.get("x"),
            "threads_id": results.get("threads"),
            "linkedin_id": results.get("linkedin"),
            "errors": errors,
        }
    return {
        "ok": True,
        "x_id": results["x"],
        "threads_id": results["threads"],
        "linkedin_id": results["linkedin"],
    }


class LaunchBundleRequest(BaseModel):
    launch_content: str
    market: str | None = None
    topic: str | None = None      # optional headline/keyword hint
    brand: str = "hitpay"
    channels: list[str] = ["blog", "x", "threads", "linkedin"]


@app.post("/api/generate-launch-bundle")
async def api_generate_launch_bundle(body: LaunchBundleRequest, user_email: str = Depends(require_auth)):
    """From one launch document (EDM or PRD), generate the selected channels (blog + X,
    Threads, LinkedIn drafts) in one shot. Everything is auto-saved, tagged
    source='product_launch', and social drafts are linked back to the blog when one is
    generated. Streams progress via SSE."""
    from src.brand_config import get_brand_config
    from src.linkedin_generator import generate_linkedin_from_changelog

    launch = (body.launch_content or "").strip()
    market = body.market or None
    brand = body.brand or "hitpay"
    channels = set(body.channels or [])
    want_blog = "blog" in channels
    want_x = "x" in channels
    want_threads = "threads" in channels
    want_linkedin = "linkedin" in channels

    # Keyword hint drives research relevance + the topic angle; fall back to the
    # launch's first non-empty line (its headline).
    topic = (body.topic or "").strip()
    if not topic:
        topic = next((ln.strip() for ln in launch.splitlines() if ln.strip()), "product launch")[:150]

    THREAD_SEP = "\n\n---\n\n"

    async def stream():
        loop = asyncio.get_event_loop()
        messages: list[str] = []

        def on_status(msg: str):
            messages.append(msg)

        try:
            if not launch:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Launch content is empty'})}\n\n"
                return
            if not (want_blog or want_x or want_threads or want_linkedin):
                yield f"data: {json.dumps({'type': 'error', 'message': 'Select at least one channel'})}\n\n"
                return

            bc = get_brand_config(brand)
            blog_id = None
            blog_title = None
            link_warnings = None
            # When no blog is generated, X's link reply points at the brand blog homepage.
            blog_url = bc.blog_base_url.rstrip("/")

            if want_blog:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Generating blog post from your launch…'})}\n\n"
                post_data = await loop.run_in_executor(
                    None,
                    lambda: generate_blog_post(topic, country=market, on_status=on_status, brand=brand, source_material=launch),
                )
                for msg in messages:
                    yield f"data: {json.dumps({'type': 'status', 'message': msg})}\n\n"

                existing = get_post_by_slug(post_data["slug"])
                if existing:
                    import time
                    post_data["slug"] = f"{post_data['slug']}-{int(time.time())}"

                post_data["editor_email"] = user_email
                post_data["source"] = "product_launch"
                file_path = write_post_file(post_data)
                blog_id = save_post(post_data, file_path)
                post_data["id"] = blog_id
                blog_title = post_data["title"]
                blog_url = bc.blog_base_url.rstrip("/") + "/" + post_data["slug"]
                link_warnings = post_data.get("link_warnings")
                log_audit(blog_id, user_email, "created", {"source": "product-launch", "topic": topic})
                yield f"data: {json.dumps({'type': 'status', 'message': 'Blog ready.', 'blog_id': blog_id, 'title': blog_title})}\n\n"

            if want_x or want_threads or want_linkedin:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Generating social drafts…'})}\n\n"

            def _choice_tweets(choice):
                if not choice:
                    return []
                tws = choice.get("tweets") or ([choice["tweet"]] if choice.get("tweet") else [])
                tws = [t for t in tws if t]
                if choice.get("link_reply"):
                    tws.append(choice["link_reply"])
                return [_cap_tweet_post_url(t.replace("[URL]", blog_url)) for t in tws]

            def do_x_threads():
                out = {"kind": "social", "x_id": None, "threads_id": None, "error": None}
                try:
                    res = repurpose_edm(launch, market)
                    if want_x:
                        choices = (res.get("x") or {}).get("choices") or []
                        chosen = (
                            next((c for c in choices if c.get("type") == "thread"), None)
                            or next((c for c in choices if c.get("type") == "quick_win"), None)
                            or (choices[0] if choices else None)
                        )
                        tweets = _choice_tweets(chosen)
                        if tweets:
                            xid = create_x_post(
                                content=THREAD_SEP.join(tweets), market=market, editor_email=user_email,
                                source_blog_post_id=blog_id, brand=brand, source="product_launch",
                            )
                            log_x_audit(xid, user_email, "created", {"source": "product-launch"})
                            out["x_id"] = xid
                    if want_threads:
                        threads_text = (res.get("threads") or "").strip()
                        if threads_text:
                            tid = create_threads_post(
                                content=threads_text, market=market, editor_email=user_email,
                                source_blog_post_id=blog_id, brand=brand, source="product_launch",
                            )
                            log_threads_audit(tid, user_email, "created", {"source": "product-launch"})
                            out["threads_id"] = tid
                except Exception as exc:
                    out["error"] = str(exc)
                return out

            def do_linkedin():
                out = {"kind": "linkedin", "linkedin_id": None, "error": None}
                try:
                    res = generate_linkedin_from_changelog(launch, brand=brand)
                    content = (res.get("content") or "").strip()
                    if content:
                        lid = create_linkedin_post(
                            content=content, market=market, editor_email=user_email,
                            source_blog_post_id=blog_id, brand=brand, source="product_launch",
                        )
                        log_linkedin_audit(lid, user_email, "created", {"source": "product-launch"})
                        out["linkedin_id"] = lid
                except Exception as exc:
                    out["error"] = str(exc)
                return out

            results = {"x_id": None, "threads_id": None, "linkedin_id": None}
            errors = {}
            futures = []
            if want_x or want_threads:
                futures.append(loop.run_in_executor(None, do_x_threads))
            if want_linkedin:
                futures.append(loop.run_in_executor(None, do_linkedin))

            for fut in asyncio.as_completed(futures):
                res = await fut
                if res.get("kind") == "social":
                    results["x_id"] = res.get("x_id")
                    results["threads_id"] = res.get("threads_id")
                    if res.get("error"):
                        errors["social"] = res["error"]
                    else:
                        made = [n for n, k in (("X", "x_id"), ("Threads", "threads_id")) if res.get(k)]
                        if made:
                            yield f"data: {json.dumps({'type': 'status', 'message': ' & '.join(made) + ' draft ready'})}\n\n"
                else:
                    results["linkedin_id"] = res.get("linkedin_id")
                    if res.get("error"):
                        errors["linkedin"] = res["error"]
                    else:
                        yield f"data: {json.dumps({'type': 'status', 'message': 'LinkedIn draft ready'})}\n\n"

            if blog_id:
                log_audit(blog_id, user_email, "launch_bundle", {
                    "x_id": results["x_id"], "threads_id": results["threads_id"],
                    "linkedin_id": results["linkedin_id"], "errors": errors or None,
                })

            done_payload = {
                "type": "done", "blog_id": blog_id, "title": blog_title or "Product launch drafts",
                "x_id": results["x_id"], "threads_id": results["threads_id"],
                "linkedin_id": results["linkedin_id"], "errors": errors or None,
            }
            if link_warnings:
                done_payload["link_warnings"] = link_warnings
            yield f"data: {json.dumps(done_payload)}\n\n"

        except Exception as e:
            _err = str(e)
            _msg = "Claude API is busy right now — please try again in a few seconds" if "overloaded_error" in _err else _err
            yield f"data: {json.dumps({'type': 'error', 'message': _msg})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Product Launch → per-step JSON endpoints ────────────────────────────────────
# The single streaming /api/generate-launch-bundle above does blog + X/Threads +
# LinkedIn in one long-lived SSE response. On Vercel's serverless Python runtime
# that connection is dropped before the (multi-minute) work finishes, surfacing in
# the browser as "Failed to fetch". These endpoints split the same work into short,
# plain-JSON request/response calls (one Claude call each) that the launch modal
# drives in sequence — each finishes well within the platform's limits.

class LaunchStepRequest(BaseModel):
    launch_content: str
    market: str | None = None
    topic: str | None = None          # blog step only: headline/keyword hint
    brand: str = "hitpay"
    blog_id: int | None = None        # social/linkedin steps: link drafts to the blog
    channels: list[str] = []          # social step: subset of {"x", "threads"}


def _launch_blog_url(brand: str, blog_id: int | None) -> str:
    """Resolve the [URL] target for social drafts: the freshly-created blog post's
    URL when we have its id, else the brand blog homepage."""
    from src.brand_config import get_brand_config
    base = get_brand_config(brand).blog_base_url.rstrip("/")
    if blog_id:
        p = get_post(blog_id)
        if p and p.get("slug"):
            return base + "/" + p["slug"]
    return base


@app.post("/api/generate-launch/blog")
def api_generate_launch_blog(body: LaunchStepRequest, user_email: str = Depends(require_auth)):
    from src.brand_config import get_brand_config
    launch = (body.launch_content or "").strip()
    if not launch:
        raise HTTPException(422, "Launch content is empty")
    brand = body.brand or "hitpay"
    market = body.market or None
    topic = (body.topic or "").strip() or next(
        (ln.strip() for ln in launch.splitlines() if ln.strip()), "product launch"
    )[:150]

    try:
        post_data = generate_blog_post(topic, country=market, brand=brand, source_material=launch)
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")

    existing = get_post_by_slug(post_data["slug"])
    if existing:
        import time
        post_data["slug"] = f"{post_data['slug']}-{int(time.time())}"

    post_data["editor_email"] = user_email
    post_data["source"] = "product_launch"
    file_path = write_post_file(post_data)
    blog_id = save_post(post_data, file_path)
    blog_url = get_brand_config(brand).blog_base_url.rstrip("/") + "/" + post_data["slug"]
    log_audit(blog_id, user_email, "created", {"source": "product-launch", "topic": topic})
    return {
        "blog_id": blog_id,
        "title": post_data["title"],
        "blog_url": blog_url,
        "link_warnings": post_data.get("link_warnings"),
    }


@app.post("/api/generate-launch/social")
def api_generate_launch_social(body: LaunchStepRequest, user_email: str = Depends(require_auth)):
    launch = (body.launch_content or "").strip()
    if not launch:
        raise HTTPException(422, "Launch content is empty")
    brand = body.brand or "hitpay"
    market = body.market or None
    channels = set(body.channels or ["x", "threads"])
    want_x = "x" in channels
    want_threads = "threads" in channels
    if not (want_x or want_threads):
        raise HTTPException(422, "Select X and/or Threads")

    blog_url = _launch_blog_url(brand, body.blog_id)
    THREAD_SEP = "\n\n---\n\n"

    def _choice_tweets(choice):
        if not choice:
            return []
        tws = choice.get("tweets") or ([choice["tweet"]] if choice.get("tweet") else [])
        tws = [t for t in tws if t]
        if choice.get("link_reply"):
            tws.append(choice["link_reply"])
        return [_cap_tweet_post_url(t.replace("[URL]", blog_url)) for t in tws]

    try:
        res = repurpose_edm(launch, market)
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")

    x_id = threads_id = None
    if want_x:
        choices = (res.get("x") or {}).get("choices") or []
        chosen = (
            next((c for c in choices if c.get("type") == "thread"), None)
            or next((c for c in choices if c.get("type") == "quick_win"), None)
            or (choices[0] if choices else None)
        )
        tweets = _choice_tweets(chosen)
        if tweets:
            x_id = create_x_post(
                content=THREAD_SEP.join(tweets), market=market, editor_email=user_email,
                source_blog_post_id=body.blog_id, brand=brand, source="product_launch",
            )
            log_x_audit(x_id, user_email, "created", {"source": "product-launch"})
    if want_threads:
        threads_text = (res.get("threads") or "").strip()
        if threads_text:
            threads_id = create_threads_post(
                content=threads_text, market=market, editor_email=user_email,
                source_blog_post_id=body.blog_id, brand=brand, source="product_launch",
            )
            log_threads_audit(threads_id, user_email, "created", {"source": "product-launch"})
    return {"x_id": x_id, "threads_id": threads_id}


@app.post("/api/generate-launch/linkedin")
def api_generate_launch_linkedin(body: LaunchStepRequest, user_email: str = Depends(require_auth)):
    from src.linkedin_generator import generate_linkedin_from_changelog
    launch = (body.launch_content or "").strip()
    if not launch:
        raise HTTPException(422, "Launch content is empty")
    brand = body.brand or "hitpay"
    market = body.market or None

    try:
        res = generate_linkedin_from_changelog(launch, brand=brand)
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")

    content = (res.get("content") or "").strip()
    linkedin_id = None
    if content:
        linkedin_id = create_linkedin_post(
            content=content, market=market, editor_email=user_email,
            source_blog_post_id=body.blog_id, brand=brand, source="product_launch",
        )
        log_linkedin_audit(linkedin_id, user_email, "created", {"source": "product-launch"})
    return {"linkedin_id": linkedin_id}


# ── Automation ────────────────────────────────────────────────────────────────

@app.post("/api/automation/weekly-post")
def api_automation_weekly_post(request: Request, dry_run: bool = True):
    """Generate and save draft posts. Called by GitHub Actions at 10am SGT (daily).

    Always generates drafts (dry_run=True is the default).
    """
    key = request.headers.get("X-Automation-Key", "")
    if not key or not AUTOMATION_SECRET or key != AUTOMATION_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    import threading
    from src.thought_leadership import generate_random_x_post
    from src.threads_thought_leadership import generate_threads_story

    _MARKETS = ["SG", "MY", "PH", None]
    x_result_box: list = []
    t_result_box: list = []

    def _gen_x():
        x_market = random.choice(_MARKETS)
        x_data = generate_random_x_post(market=x_market, brand="hitpay")
        _x_link = x_data.get("link_url") or ""
        # Cap AFTER URL substitution — [URL] placeholder is 5 chars but real URLs are 38+
        x_content = "\n\n---\n\n".join(_cap_tweet(t.replace("[URL]", _x_link)) for t in x_data["tweets"])
        x_id = create_x_post(
            content=x_content,
            market=x_data.get("market"),
            editor_email="automation@hit-pay.com",
            brand="hitpay",
        )
        log_x_audit(x_id, "automation@hit-pay.com", "created", {
            "source": "weekly_automation", "market": x_data.get("market") or "",
            "content_type": x_data.get("content_type"), "dry_run": dry_run,
        })
        x_result_box.append(x_id)

    def _gen_t():
        t_market = random.choice(_MARKETS)
        t_data = generate_threads_story(market=t_market, brand="hitpay")
        raw_posts = t_data["posts"]
        t_posts = [p["text"] if isinstance(p, dict) else str(p) for p in raw_posts]
        t_content = "\n\n---\n\n".join(t_posts)
        t_id = create_threads_post(
            content=t_content,
            market=t_data.get("market"),
            editor_email="automation@hit-pay.com",
            brand="hitpay",
        )
        log_threads_audit(t_id, "automation@hit-pay.com", "created", {
            "source": "weekly_automation", "market": t_data.get("market") or "", "dry_run": dry_run,
        })
        t_result_box.append(t_id)

    # Run both Claude generations concurrently — cuts wall-clock time roughly in half
    tx = threading.Thread(target=_gen_x)
    tt = threading.Thread(target=_gen_t)
    tx.start(); tt.start()
    tx.join(); tt.join()

    x_id = x_result_box[0]
    t_id = t_result_box[0]

    return {
        "dry_run": dry_run,
        "x_post_id": x_id,
        "threads_post_id": t_id,
    }


@app.post("/api/automation/generate-weekly-drafts")
def api_generate_weekly_drafts(request: Request):
    """Generate 7 X drafts (one per content type) + 7 Threads drafts (varied sizes).

    Called by GitHub Actions on Sunday. Posts land in Unscheduled Drafts for
    manual scheduling via the calendar drag-and-drop.
    """
    key = request.headers.get("X-Automation-Key", "")
    if not key or not AUTOMATION_SECRET or key != AUTOMATION_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    import threading
    from src.thought_leadership import generate_random_x_post, HITPAY_TOPIC_POOL, CONTENT_TYPE_BY_WEEKDAY
    from src.threads_thought_leadership import generate_threads_story

    _MARKETS = ["SG", "MY", "PH", None]
    # One post per day (Mon–Sun) following the day-of-week schedule:
    # product_focus × 3 (Mon/Thu/Sun), thought_leadership × 3 (Tue/Fri/Sat), merchant_story × 1 (Wed)
    content_types = list(CONTENT_TYPE_BY_WEEKDAY.values())
    # Varied thread sizes: 2 singles, 3 three-post, 2 five-post
    _THREAD_SIZES = [1, 1, 3, 3, 3, 5, 5]

    x_results = []
    thr_results = []
    errors = []
    lock = threading.Lock()

    def _gen_x(content_type: str):
        try:
            market = random.choice(_MARKETS)
            topic = random.choice(HITPAY_TOPIC_POOL)
            data = generate_random_x_post(market=market, topic_hint=topic, brand="hitpay", content_type=content_type)
            link = data.get("link_url") or ""
            content = "\n\n---\n\n".join(_cap_tweet(t.replace("[URL]", link)) for t in data["tweets"])
            post_id = create_x_post(
                content=content,
                market=data.get("market"),
                editor_email="automation@hit-pay.com",
                brand="hitpay",
            )
            log_x_audit(post_id, "automation@hit-pay.com", "created", {
                "source": "weekly_batch", "content_type": content_type,
                "market": data.get("market") or "", "topic": topic,
            })
            with lock:
                x_results.append({"content_type": content_type, "post_id": post_id, "market": data.get("market")})
        except Exception as e:
            with lock:
                errors.append({"platform": "x", "content_type": content_type, "error": str(e)})

    def _gen_threads(thread_size: int):
        try:
            market = random.choice(_MARKETS)
            topic = random.choice(HITPAY_TOPIC_POOL)
            data = generate_threads_story(market=market, topic_hint=topic, brand="hitpay", thread_size=thread_size)
            link = data.get("link_url") or ""
            posts = data.get("posts", [])
            content = "\n\n---\n\n".join(p.replace("[URL]", link) for p in posts)
            post_id = create_threads_post(
                content=content,
                market=data.get("market") or market,
                editor_email="automation@hit-pay.com",
                brand="hitpay",
            )
            log_threads_audit(post_id, "automation@hit-pay.com", "created", {
                "source": "weekly_batch", "thread_size": thread_size,
                "market": data.get("market") or market or "", "topic": topic,
            })
            with lock:
                thr_results.append({"thread_size": thread_size, "post_id": post_id, "market": data.get("market") or market})
        except Exception as e:
            with lock:
                errors.append({"platform": "threads", "thread_size": thread_size, "error": str(e)})

    all_threads = (
        [threading.Thread(target=_gen_x, args=(ct,)) for ct in content_types] +
        [threading.Thread(target=_gen_threads, args=(sz,)) for sz in _THREAD_SIZES]
    )
    for t in all_threads: t.start()
    for t in all_threads: t.join()

    return {
        "x": {"generated": x_results, "total": len(x_results)},
        "threads": {"generated": thr_results, "total": len(thr_results)},
        "errors": errors,
    }


# ── YouTube description generator ───────────────────────────────────────────

class GenerateYoutubeDescriptionRequest(BaseModel):
    video_info: str
    market: str | None = None
    brand: str = "hitpay"
    video_type: str = "video"
    is_case_study: bool = False
    merchant_brand_name: str | None = None


@app.post("/api/youtube-descriptions/generate")
def api_generate_youtube_description(
    body: GenerateYoutubeDescriptionRequest,
    user_email: str = Depends(require_auth),
):
    from src.youtube_description import generate_youtube_description

    try:
        result = generate_youtube_description(
            body.video_info,
            market=body.market or None,
            brand=body.brand,
            video_type=body.video_type,
            is_case_study=body.is_case_study,
            merchant_brand_name=body.merchant_brand_name,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        if "overloaded_error" in str(e):
            raise HTTPException(503, "Claude API is busy right now — please try again in a few seconds")
        raise HTTPException(500, f"Generation error: {e}")

    entry_id = save_youtube_description(
        video_info=body.video_info,
        description=result["description"],
        market=result["market"],
        source_post_id=result["source_post_id"],
        source_post_slug=result["source_post_slug"],
        source_post_title=result["source_post_title"],
        editor_email=user_email,
        brand=body.brand,
        title=result["title"],
        video_type=result["video_type"],
    )
    result["id"] = entry_id
    return result


@app.get("/api/youtube-descriptions")
def api_list_youtube_descriptions(
    market: str = None, brand: str = "hitpay", _: str = Depends(require_auth)
):
    return list_youtube_descriptions(market=market, brand=brand)


class UpdateYoutubeDescriptionRequest(BaseModel):
    video_info: str | None = None
    market: str | None = None
    description: str | None = None
    title: str | None = None


@app.get("/api/youtube-descriptions/{entry_id}")
def api_get_youtube_description(entry_id: int, _: str = Depends(require_auth)):
    entry = get_youtube_description(entry_id)
    if not entry:
        raise HTTPException(404, "Not found")
    return entry


@app.put("/api/youtube-descriptions/{entry_id}")
def api_update_youtube_description(
    entry_id: int,
    body: UpdateYoutubeDescriptionRequest,
    user_email: str = Depends(require_auth),
):
    entry = get_youtube_description(entry_id)
    if not entry:
        raise HTTPException(404, "Not found")
    fields = {}
    if body.video_info is not None:
        fields["video_info"] = body.video_info.strip()
    if body.market is not None:
        fields["market"] = body.market or None
    if body.description is not None:
        fields["description"] = body.description.strip()
    if body.title is not None:
        fields["title"] = body.title.strip()
    if fields:
        update_youtube_description(entry_id, fields)
    return {"ok": True}


@app.delete("/api/youtube-descriptions/{entry_id}")
def api_delete_youtube_description(entry_id: int, _: str = Depends(require_auth)):
    delete_youtube_description(entry_id)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
