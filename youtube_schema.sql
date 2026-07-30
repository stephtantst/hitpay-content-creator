-- Run this in your Supabase SQL editor to create the YouTube description module table.

CREATE TABLE IF NOT EXISTS youtube_descriptions (
  id                SERIAL PRIMARY KEY,
  video_info        TEXT NOT NULL,
  market            VARCHAR(10),
  brand             VARCHAR(50) NOT NULL DEFAULT 'hitpay',
  title             VARCHAR(300),
  video_type        VARCHAR(30) NOT NULL DEFAULT 'video',
  description       TEXT NOT NULL,
  source_post_id    INTEGER REFERENCES posts(id) ON DELETE SET NULL,
  source_post_slug  VARCHAR(300),
  source_post_title VARCHAR(300),
  editor_email      VARCHAR(200),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_youtube_descriptions_created ON youtube_descriptions (created_at DESC);
