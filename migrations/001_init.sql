PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  repo_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_events (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id TEXT,
  turn_id TEXT,
  agent_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  memory_extract_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (memory_extract_status IN ('pending', 'done')),
  memory_extract_error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_items (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  source_event_id TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(project_id, key)
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'in_progress', 'completed', 'cancelled')),
  priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
  source_event_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  topic TEXT NOT NULL,
  decision TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'superseded')),
  superseded_by TEXT REFERENCES decisions(id),
  source_event_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_logs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  session_id TEXT,
  agent_id TEXT,
  summary TEXT NOT NULL,
  changed_files_json TEXT NOT NULL DEFAULT '[]',
  commit_hash TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_events_project_time
ON raw_events(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status
ON tasks(project_id, status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_project_status
ON decisions(project_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_logs_project_time
ON work_logs(project_id, created_at DESC);
