import json

from src.database import get_connection, _rows_to_dicts

DEFAULT_SUBREDDIT = "r/HitPay_official"


def list_reddit_posts(status: str = None, market: str = None, brand: str = None) -> list:
    conn = get_connection()
    conn.run(
        "UPDATE reddit_posts SET status = 'posted', posted_at = scheduled_at, updated_at = NOW() "
        "WHERE status = 'scheduled' AND scheduled_at IS NOT NULL AND scheduled_at < NOW()"
    )
    clauses, params = [], {}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if market:
        clauses.append("(market = :market OR market IS NULL OR market = '')")
        params["market"] = market
    if brand:
        safe = brand.replace("'", "''")
        clauses.append(f"(brand = '{safe}' OR brand IS NULL)")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.run(f"SELECT * FROM reddit_posts {where} ORDER BY created_at DESC", **params)
    return _rows_to_dicts(conn, rows)


def get_reddit_post(post_id: int) -> dict | None:
    conn = get_connection()
    rows = conn.run("SELECT * FROM reddit_posts WHERE id = :id", id=post_id)
    result = _rows_to_dicts(conn, rows)
    return result[0] if result else None


def get_reddit_posts_by_blog_post_id(blog_post_id: int) -> list:
    conn = get_connection()
    rows = conn.run(
        f"SELECT * FROM reddit_posts WHERE source_blog_post_id = {int(blog_post_id)} ORDER BY created_at DESC"
    )
    return _rows_to_dicts(conn, rows)


def create_reddit_post(content: str, title: str = None, subreddit: str = None,
                       reply_comment: str = None, market: str = None, scheduled_at=None,
                       editor_email: str = None, source_blog_post_id: int = None,
                       brand: str = "hitpay", source: str = None) -> int:
    conn = get_connection()
    rows = conn.run(
        """
        INSERT INTO reddit_posts
            (content, title, subreddit, reply_comment, market, brand, status,
             scheduled_at, editor_email, source_blog_post_id, source)
        VALUES
            (:content, :title, :subreddit, :reply_comment, :market, :brand, 'draft',
             :scheduled_at, :editor_email, :source_blog_post_id, :source)
        RETURNING id
        """,
        content=content,
        title=title or None,
        subreddit=subreddit or DEFAULT_SUBREDDIT,
        reply_comment=reply_comment or None,
        market=market or None,
        brand=brand,
        scheduled_at=scheduled_at,
        editor_email=editor_email,
        source_blog_post_id=source_blog_post_id,
        source=source,
    )
    return rows[0][0]


def update_reddit_post(post_id: int, fields: dict):
    if not fields:
        return
    set_clauses = ", ".join([f"{k} = :{k}" for k in fields.keys()])
    conn = get_connection()
    conn.run(
        f"UPDATE reddit_posts SET {set_clauses}, updated_at = NOW() WHERE id = :id",
        **fields,
        id=post_id,
    )


def change_reddit_post_status(post_id: int, new_status: str, scheduled_at=None, post_url: str = None):
    conn = get_connection()
    fields = {"status": new_status}
    if new_status == "scheduled" and scheduled_at is not None:
        fields["scheduled_at"] = scheduled_at
    if new_status == "posted":
        fields["posted_at"] = "NOW()"
        if post_url:
            fields["post_url"] = post_url
    set_clauses, params = [], {"id": post_id}
    for k, v in fields.items():
        if v == "NOW()":
            set_clauses.append(f"{k} = NOW()")
        else:
            set_clauses.append(f"{k} = :{k}")
            params[k] = v
    set_sql = ", ".join(set_clauses) + ", updated_at = NOW()"
    conn.run(f"UPDATE reddit_posts SET {set_sql} WHERE id = :id", **params)


def delete_reddit_post(post_id: int):
    conn = get_connection()
    conn.run("DELETE FROM reddit_posts WHERE id = :id", id=post_id)


def log_reddit_audit(post_id: int, user_email: str, action: str, details: dict = None):
    conn = get_connection()
    if action == "edited":
        rows = conn.run(
            """
            SELECT id FROM reddit_audit_log
            WHERE post_id = :post_id AND user_email = :user_email AND action = 'edited'
            AND timestamp > NOW() - INTERVAL '5 minutes'
            ORDER BY timestamp DESC LIMIT 1
            """,
            post_id=post_id,
            user_email=user_email,
        )
        if rows:
            conn.run(
                "UPDATE reddit_audit_log SET timestamp = NOW(), details = :details WHERE id = :id",
                details=json.dumps(details) if details else None,
                id=rows[0][0],
            )
            return
    conn.run(
        "INSERT INTO reddit_audit_log (post_id, user_email, action, details) "
        "VALUES (:post_id, :user_email, :action, :details)",
        post_id=post_id,
        user_email=user_email,
        action=action,
        details=json.dumps(details) if details else None,
    )


def get_reddit_audit_log(post_id: int) -> list:
    conn = get_connection()
    rows = conn.run(
        "SELECT * FROM reddit_audit_log WHERE post_id = :post_id ORDER BY timestamp DESC LIMIT 50",
        post_id=post_id,
    )
    return _rows_to_dicts(conn, rows)
