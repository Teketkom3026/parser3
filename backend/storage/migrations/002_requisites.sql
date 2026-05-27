-- Adds ОГРН and requisites-based company name columns (п.4)
-- SQLite does not support IF NOT EXISTS for ALTER TABLE ADD COLUMN,
-- so the migration runner must tolerate "duplicate column name" errors.
ALTER TABLE contacts ADD COLUMN ogrn TEXT;
ALTER TABLE contacts ADD COLUMN req_company_name TEXT;
