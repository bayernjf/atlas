CREATE TABLE IF NOT EXISTS iam_users (
  tenant_id     TEXT NOT NULL,
  username      TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  display_name  TEXT NOT NULL,
  role          TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (tenant_id, username)
);
