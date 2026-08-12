import json
import urllib.parse

import pg8000.native

from config import DATABASE_URL


def _parse_url(url: str) -> dict:
    """Parse a postgres:// URL into pg8000.native.Connection kwargs."""
    p = urllib.parse.urlparse(url)
    kwargs = {
        "host": p.hostname,
        "port": p.port or 5432,
        "database": p.path.lstrip("/"),
        "user": p.username,
        "password": p.password,
        "ssl_context": True,
    }
    return kwargs


def get_connection():
    return pg8000.native.Connection(**_parse_url(DATABASE_URL))


def _rows_to_dicts(conn, rows) -> list:
    """pg8000.native returns rows as lists; use conn.columns for names."""
    if not rows:
        return []
    keys = [c["name"] for c in conn.columns]
    return [dict(zip(keys, row)) for row in rows]


def init_db():
    """No-op: tables are pre-created in Supabase."""
    pass


def migrate_brand_column():
    """Add brand column to posts + social post tables if missing."""
    conn = get_connection()
    for table in ("posts", "x_posts", "threads_posts", "linkedin_posts", "reddit_posts"):
        conn.run(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS brand VARCHAR(50) DEFAULT 'hitpay'"
        )


def migrate_source_blog_post_id():
    """Add source_blog_post_id FK to social post tables if missing."""
    conn = get_connection()
    for table in ("x_posts", "threads_posts", "linkedin_posts", "reddit_posts"):
        conn.run(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS source_blog_post_id INTEGER"
        )


def migrate_youtube_descriptions_table():
    """Create the youtube_descriptions table if it doesn't exist yet."""
    conn = get_connection()
    conn.run(
        """
        CREATE TABLE IF NOT EXISTS youtube_descriptions (
          id                SERIAL PRIMARY KEY,
          video_info        TEXT NOT NULL,
          market            VARCHAR(10),
          brand             VARCHAR(50) NOT NULL DEFAULT 'hitpay',
          description       TEXT NOT NULL,
          source_post_id    INTEGER REFERENCES posts(id) ON DELETE SET NULL,
          source_post_slug  VARCHAR(300),
          source_post_title VARCHAR(300),
          editor_email      VARCHAR(200),
          created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.run(
        "CREATE INDEX IF NOT EXISTS idx_youtube_descriptions_created ON youtube_descriptions (created_at DESC)"
    )


def migrate_youtube_title_column():
    """Add title + video_type columns to youtube_descriptions if missing."""
    conn = get_connection()
    conn.run("ALTER TABLE youtube_descriptions ADD COLUMN IF NOT EXISTS title VARCHAR(300)")
    conn.run(
        "ALTER TABLE youtube_descriptions ADD COLUMN IF NOT EXISTS video_type VARCHAR(30) NOT NULL DEFAULT 'video'"
    )


def migrate_source_column():
    """Add a `source` provenance column to posts + social tables if missing.

    Used to tag content generated from a product launch (source='product_launch').
    """
    conn = get_connection()
    for table in ("posts", "x_posts", "threads_posts", "linkedin_posts", "reddit_posts"):
        conn.run(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS source VARCHAR(50)"
        )


def save_feedback(user_email: str, message: str) -> int:
    conn = get_connection()
    rows = conn.run(
        "INSERT INTO feedback (user_email, message) VALUES (:user_email, :message) RETURNING id",
        user_email=user_email,
        message=message,
    )
    return rows[0][0]


def list_feedback() -> list:
    conn = get_connection()
    rows = conn.run("SELECT * FROM feedback ORDER BY submitted_at DESC")
    return _rows_to_dicts(conn, rows)


def log_login(email: str, name: str):
    conn = get_connection()
    conn.run(
        "INSERT INTO login_log (email, name) VALUES (:email, :name)",
        email=email,
        name=name,
    )


def list_logins() -> list:
    conn = get_connection()
    rows = conn.run("SELECT * FROM login_log ORDER BY logged_in_at DESC LIMIT 200")
    return _rows_to_dicts(conn, rows)


def log_audit(post_id, user_email: str, action: str, details: dict = None):
    conn = get_connection()
    if action == "edited":
        rows = conn.run(
            """
            SELECT id FROM audit_log
            WHERE post_id = :post_id AND user_email = :user_email AND action = 'edited'
            AND timestamp > NOW() - INTERVAL '5 minutes'
            ORDER BY timestamp DESC LIMIT 1
            """,
            post_id=post_id,
            user_email=user_email,
        )
        if rows:
            conn.run(
                "UPDATE audit_log SET timestamp = NOW(), details = :details WHERE id = :id",
                details=json.dumps(details) if details else None,
                id=rows[0][0],
            )
            return
    conn.run(
        "INSERT INTO audit_log (post_id, user_email, action, details) VALUES (:post_id, :user_email, :action, :details)",
        post_id=post_id,
        user_email=user_email,
        action=action,
        details=json.dumps(details) if details else None,
    )


def get_audit_log(post_id: int) -> list:
    conn = get_connection()
    rows = conn.run(
        "SELECT * FROM audit_log WHERE post_id = :post_id ORDER BY timestamp DESC LIMIT 50",
        post_id=post_id,
    )
    return _rows_to_dicts(conn, rows)


def save_post(post_data: dict, file_path: str) -> int:
    conn = get_connection()
    rows = conn.run(
        """
        INSERT INTO posts (title, slug, keyword, country, brand, status, date, meta_title,
                           meta_description, overview, categories, tags, file_path, word_count, content, source_url,
                           editor_email, source)
        VALUES (:title, :slug, :keyword, :country, :brand, :status, :date, :meta_title,
                :meta_description, :overview, :categories, :tags, :file_path, :word_count, :content, :source_url,
                :editor_email, :source)
        RETURNING id
        """,
        title=post_data["title"],
        slug=post_data["slug"],
        keyword=post_data.get("keyword", ""),
        country=post_data.get("country", ""),
        brand=post_data.get("brand", "hitpay"),
        status=post_data.get("status", "writing"),
        date=post_data["date"],
        meta_title=post_data.get("meta_title", ""),
        meta_description=post_data.get("meta_description", ""),
        overview=post_data.get("overview", ""),
        categories=json.dumps(post_data.get("categories", [])),
        tags=json.dumps(post_data.get("tags", [])),
        file_path=file_path,
        word_count=len(post_data.get("content", "").split()),
        content=post_data.get("content", ""),
        source_url=post_data.get("source_url", ""),
        editor_email=post_data.get("editor_email", None),
        source=post_data.get("source", None),
    )
    return rows[0][0]


def get_post(post_id: int) -> dict | None:
    conn = get_connection()
    rows = conn.run("SELECT * FROM posts WHERE id = :id", id=post_id)
    result = _rows_to_dicts(conn, rows)
    return result[0] if result else None


def get_post_by_slug(slug: str) -> dict | None:
    conn = get_connection()
    rows = conn.run("SELECT * FROM posts WHERE slug = :slug", slug=slug)
    result = _rows_to_dicts(conn, rows)
    return result[0] if result else None


def list_posts(status: str = None, brand: str = None) -> list:
    conn = get_connection()
    clauses = []
    params = {}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if brand:
        # Embed directly to stay on simple-query protocol (PgBouncer transaction mode
        # drops unnamed prepared statements between round-trips).
        safe = brand.replace("'", "''")
        clauses.append(f"(brand = '{safe}' OR brand IS NULL)")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.run(f"SELECT * FROM posts {where} ORDER BY created_at DESC", **params)
    return _rows_to_dicts(conn, rows)


def update_post_status(post_id: int, new_status: str, old_file_path: str, new_file_path: str, editor_email=None):
    conn = get_connection()
    conn.run(
        "UPDATE posts SET status = :status, file_path = :file_path, editor_email = :editor_email, updated_at = NOW() WHERE id = :id",
        status=new_status,
        file_path=new_file_path,
        editor_email=editor_email,
        id=post_id,
    )


def update_post_fields(post_id: int, fields: dict):
    if not fields:
        return
    set_clauses = ", ".join([f"{k} = :{k}" for k in fields.keys()])
    conn = get_connection()
    conn.run(
        f"UPDATE posts SET {set_clauses}, updated_at = NOW() WHERE id = :id",
        **fields,
        id=post_id,
    )


def delete_post(post_id: int):
    conn = get_connection()
    conn.run("DELETE FROM posts WHERE id = :id", id=post_id)


def get_repurposed_content(post_id: int) -> dict | None:
    conn = get_connection()
    rows = conn.run(
        "SELECT repurposed_content FROM posts WHERE id = :id", id=post_id
    )
    if not rows or rows[0][0] is None:
        return None
    val = rows[0][0]
    return val if isinstance(val, dict) else json.loads(val)


def get_unrepurposed_published_post(brand: str = "hitpay") -> dict | None:
    """Return a random published post that has not yet been repurposed for X."""
    conn = get_connection()
    safe = brand.replace("'", "''")
    rows = conn.run(
        f"""SELECT * FROM posts
            WHERE status = 'published'
            AND x_repurposed_at IS NULL
            AND (brand = '{safe}' OR brand IS NULL)
            ORDER BY RANDOM()
            LIMIT 1"""
    )
    result = _rows_to_dicts(conn, rows)
    return result[0] if result else None


def mark_post_x_repurposed(post_id: int):
    """Record that a post has been repurposed into an X draft."""
    conn = get_connection()
    conn.run(
        "UPDATE posts SET x_repurposed_at = NOW(), updated_at = NOW() WHERE id = :id",
        id=post_id,
    )


def migrate_x_repurposed_column():
    """Add x_repurposed_at column to posts if missing."""
    conn = get_connection()
    conn.run(
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS x_repurposed_at TIMESTAMPTZ"
    )


def update_repurposed_content(post_id: int, platform: str | None,
                              data: dict, replace_all: bool = False):
    conn = get_connection()
    if replace_all:
        conn.run(
            "UPDATE posts SET repurposed_content = :data, updated_at = NOW() WHERE id = :id",
            data=json.dumps(data),
            id=post_id,
        )
    else:
        existing_rows = conn.run(
            "SELECT repurposed_content FROM posts WHERE id = :id", id=post_id
        )
        existing = {}
        if existing_rows and existing_rows[0][0]:
            val = existing_rows[0][0]
            existing = val if isinstance(val, dict) else json.loads(val)
        existing[platform] = data
        conn.run(
            "UPDATE posts SET repurposed_content = :data, updated_at = NOW() WHERE id = :id",
            data=json.dumps(existing),
            id=post_id,
        )
