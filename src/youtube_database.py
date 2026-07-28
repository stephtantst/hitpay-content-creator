from src.database import get_connection, _rows_to_dicts


def list_youtube_descriptions(market: str = None, brand: str = None) -> list:
    conn = get_connection()
    clauses = []
    params = {}
    if market:
        clauses.append("market = :market")
        params["market"] = market
    if brand:
        safe = brand.replace("'", "''")
        clauses.append(f"(brand = '{safe}' OR brand IS NULL)")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.run(
        f"SELECT * FROM youtube_descriptions {where} ORDER BY created_at DESC LIMIT 200",
        **params,
    )
    return _rows_to_dicts(conn, rows)


def get_youtube_description(entry_id: int) -> dict | None:
    conn = get_connection()
    rows = conn.run("SELECT * FROM youtube_descriptions WHERE id = :id", id=entry_id)
    result = _rows_to_dicts(conn, rows)
    return result[0] if result else None


def save_youtube_description(
    video_info: str,
    description: str,
    market: str = None,
    source_post_id: int = None,
    source_post_slug: str = None,
    source_post_title: str = None,
    editor_email: str = None,
    brand: str = "hitpay",
) -> int:
    conn = get_connection()
    rows = conn.run(
        """
        INSERT INTO youtube_descriptions
            (video_info, description, market, brand, source_post_id, source_post_slug,
             source_post_title, editor_email)
        VALUES
            (:video_info, :description, :market, :brand, :source_post_id, :source_post_slug,
             :source_post_title, :editor_email)
        RETURNING id
        """,
        video_info=video_info,
        description=description,
        market=market or None,
        brand=brand,
        source_post_id=source_post_id,
        source_post_slug=source_post_slug,
        source_post_title=source_post_title,
        editor_email=editor_email,
    )
    return rows[0][0]


def delete_youtube_description(entry_id: int):
    conn = get_connection()
    conn.run("DELETE FROM youtube_descriptions WHERE id = :id", id=entry_id)
