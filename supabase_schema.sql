-- Updated schema — single source of truth for Supabase tables

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    industry TEXT,
    size TEXT,
    settings JSONB DEFAULT '{}'::jsonb,
    telegram_chat_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'read_only'
        CHECK (role IN ('master_admin', 'org_admin', 'read_write', 'read_only')),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE folders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES folders(id),
    name TEXT NOT NULL,
    is_essential BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    folder_id UUID REFERENCES folders(id),
    name TEXT NOT NULL,
    mime_type TEXT,
    is_deleted BOOLEAN NOT NULL DEFAULT false,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE file_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL DEFAULT 1,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    sha256 TEXT,
    message_ids JSONB DEFAULT '[]'::jsonb,
    uploaded_by UUID REFERENCES users(id),
    uploaded_at TIMESTAMPTZ DEFAULT now(),
    is_current BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (file_id, version_number)
);

CREATE TABLE permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    folder_id UUID REFERENCES folders(id) ON DELETE CASCADE,
    permission_level TEXT NOT NULL DEFAULT 'read_only'
        CHECK (permission_level IN ('read_only', 'read_write', 'org_admin')),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (user_id, folder_id)
);

CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    actor_id UUID REFERENCES users(id),
    actor_role TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Shared links (public shareable file links)
CREATE TABLE shared_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE DEFAULT encode(gen_random_bytes(16), 'hex'),
    created_by UUID REFERENCES users(id),
    expires_at TIMESTAMPTZ,
    password_hash TEXT,
    download_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Backups — metadata snapshots stored as JSON on Telegram channel
CREATE TABLE backups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    size_bytes INTEGER,
    message_id INTEGER NOT NULL,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Version limit trigger: keep max 5 versions per file, FIFO
CREATE OR REPLACE FUNCTION trg_enforce_version_limit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    DELETE FROM file_versions fv
    WHERE fv.file_id = NEW.file_id
      AND fv.id NOT IN (
          SELECT id FROM file_versions
          WHERE file_id = NEW.file_id
          ORDER BY version_number DESC
          LIMIT 5
      );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_enforce_version_limit
    AFTER INSERT ON file_versions
    FOR EACH ROW EXECUTE FUNCTION trg_enforce_version_limit();

-- RLS
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE files ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE shared_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE backups ENABLE ROW LEVEL SECURITY;

-- Performance indexes (PostgreSQL does NOT auto-index FK columns)
CREATE INDEX IF NOT EXISTS idx_files_org_id ON files(org_id);
CREATE INDEX IF NOT EXISTS idx_files_folder_id ON files(folder_id);
CREATE INDEX IF NOT EXISTS idx_file_versions_file_id ON file_versions(file_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_org_created ON audit_logs(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor_id);
CREATE INDEX IF NOT EXISTS idx_permissions_org_user ON permissions(org_id, user_id);
CREATE INDEX IF NOT EXISTS idx_permissions_folder ON permissions(folder_id);
CREATE INDEX IF NOT EXISTS idx_backups_org_id ON backups(org_id);
CREATE INDEX IF NOT EXISTS idx_shared_links_token ON shared_links(token);

-- Org isolation policies (unique names per table, master_admin bypass on all)

CREATE POLICY orgs_org_isolation ON organizations
    FOR ALL USING (
        id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY users_select_all ON users
    FOR SELECT USING (true);

CREATE POLICY users_org_isolation ON users
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY folders_org_isolation ON folders
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY files_org_isolation ON files
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY file_versions_org_isolation ON file_versions
    FOR ALL USING (
        file_id IN (
            SELECT f.id FROM files f
            JOIN users u ON f.org_id = u.org_id
            WHERE u.id = current_setting('app.user_id')::UUID
        )
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY permissions_org_isolation ON permissions
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY audit_logs_org_isolation ON audit_logs
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY shared_links_org_isolation ON shared_links
    FOR ALL USING (
        file_id IN (
            SELECT f.id FROM files f
            JOIN users u ON f.org_id = u.org_id
            WHERE u.id = current_setting('app.user_id')::UUID
        )
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY backups_org_isolation ON backups
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

-- Helper to set user context for RLS
CREATE OR REPLACE FUNCTION set_app_context(uid UUID, urole TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM set_config('app.user_id', uid::text, true);
    PERFORM set_config('app.user_role', urole, true);
END;
$$;
