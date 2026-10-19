"""Version 1 of the local evidence database contract."""

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE tasks (id TEXT PRIMARY KEY, state TEXT NOT NULL, anchor TEXT NOT NULL);
CREATE TABLE task_versions (
 task_id TEXT NOT NULL REFERENCES tasks(id), version INTEGER NOT NULL,
 state TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(task_id, version));
CREATE TABLE sources (
 key TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), path TEXT NOT NULL,
 agent TEXT NOT NULL, session_id TEXT NOT NULL, cursor TEXT NOT NULL);
CREATE TABLE events (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 source_key TEXT NOT NULL REFERENCES sources(key), sequence INTEGER NOT NULL,
 kind TEXT NOT NULL, text TEXT NOT NULL, payload TEXT NOT NULL,
 UNIQUE(source_key, sequence));
CREATE INDEX events_task ON events(task_id);
CREATE VIRTUAL TABLE evidence_fts USING fts5(id UNINDEXED, task_id UNINDEXED, text);
CREATE TABLE observations (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE handoffs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), payload TEXT NOT NULL,
 prompt_digest TEXT NOT NULL);
CREATE INDEX handoffs_prompt ON handoffs(task_id,prompt_digest);
PRAGMA user_version=1;
"""
