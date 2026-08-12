-- Run this in your Supabase SQL editor to create the Reddit posts tables.
--
-- Reddit deliverables differ from the other platforms: each post is a
-- merchant-voice OP (title + body, NO HitPay branding) plus a SEPARATE
-- HitPay reply comment posted from the verified account.
--   content        = OP body (merchant voice) — reuses the shared status/
--                    scheduling/audit/char-count machinery like every platform
--   title          = OP title
--   subreddit      = target subreddit (defaults to r/HitPay_official)
--   reply_comment  = HitPay verified-account reply comment

CREATE TABLE IF NOT EXISTS reddit_posts (
  id                    SERIAL PRIMARY KEY,
  content               TEXT NOT NULL,
  title                 TEXT,
  subreddit             VARCHAR(100) DEFAULT 'r/HitPay_official',
  reply_comment         TEXT,
  market                VARCHAR(10),
  brand                 VARCHAR(20) DEFAULT 'hitpay',
  status                VARCHAR(20) NOT NULL DEFAULT 'draft',
  scheduled_at          TIMESTAMPTZ,
  posted_at             TIMESTAMPTZ,
  post_url              VARCHAR(500),
  editor_email          VARCHAR(200),
  source                VARCHAR(50),
  source_blog_post_id   INTEGER REFERENCES posts(id) ON DELETE SET NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reddit_audit_log (
  id          SERIAL PRIMARY KEY,
  post_id     INTEGER NOT NULL REFERENCES reddit_posts(id) ON DELETE CASCADE,
  user_email  VARCHAR(200),
  action      VARCHAR(50) NOT NULL,
  details     JSONB,
  timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reddit_posts_status   ON reddit_posts (status);
CREATE INDEX IF NOT EXISTS idx_reddit_audit_log_post ON reddit_audit_log (post_id);
