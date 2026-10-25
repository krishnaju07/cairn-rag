CREATE TABLE IF NOT EXISTS pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    draft TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_versions (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    steps TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (pipeline_id, version)
);

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
    pipeline_version INTEGER NOT NULL,
    embed_model TEXT NOT NULL,
    answer_model TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL DEFAULT 'hybrid',
    top_k INTEGER NOT NULL DEFAULT 5,
    instructions TEXT NOT NULL DEFAULT '',
    max_answer_tokens INTEGER NOT NULL DEFAULT 600,
    min_relevance REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    ext TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    pipeline_version INTEGER NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    paged INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    start_char INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (document_id, page)
);

CREATE TABLE IF NOT EXISTS doc_steps (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (document_id, position)
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    collection_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'chunk',
    parent_id INTEGER,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    embedding BLOB
);

CREATE INDEX IF NOT EXISTS chunks_collection ON chunks(collection_id);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    collection_id UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queries (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    answered INTEGER NOT NULL,
    citations TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL
);
