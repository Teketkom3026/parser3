CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'all_contacts',
    status TEXT NOT NULL DEFAULT 'pending',
    input_file TEXT,
    target_positions TEXT,
    total_urls INTEGER NOT NULL DEFAULT 0,
    processed_urls INTEGER NOT NULL DEFAULT 0,
    found_contacts INTEGER NOT NULL DEFAULT 0,
    errors_count INTEGER NOT NULL DEFAULT 0,
    output_file TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at DESC);

CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error_code TEXT,
    error_message TEXT,
    pages_visited INTEGER DEFAULT 0,
    contacts_found INTEGER DEFAULT 0,
    processing_time_ms INTEGER,
    retries INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, url)
);
CREATE INDEX IF NOT EXISTS idx_sites_task_status ON sites(task_id, status);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    site_id INTEGER,
    domain TEXT NOT NULL,
    page_url TEXT NOT NULL,
    company_name TEXT,
    company_email TEXT,
    company_phone TEXT,
    full_name TEXT,
    last_name TEXT,
    first_name TEXT,
    patronymic TEXT,
    gender TEXT,
    position_raw TEXT,
    position_canonical TEXT,
    role_category TEXT,
    matched_entry_id TEXT,
    norm_method TEXT,
    sheet_name TEXT,
    person_email TEXT,
    person_phone TEXT,
    inn TEXT,
    kpp TEXT,
    social_links TEXT,
    language TEXT,
    status TEXT,
    comment TEXT,
    dedup_key TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_contacts_task ON contacts(task_id);
CREATE INDEX IF NOT EXISTS idx_contacts_dedup ON contacts(task_id, dedup_key);

CREATE TABLE IF NOT EXISTS blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type TEXT NOT NULL,
    entry_value TEXT NOT NULL UNIQUE,
    source TEXT DEFAULT 'user',
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_blacklist_type ON blacklist(entry_type);
