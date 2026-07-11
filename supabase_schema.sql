-- Updated schema — single source of truth for Supabase tables

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    industry TEXT,
    size TEXT,
    settings JSONB DEFAULT '{}'::jsonb,
    telegram_chat_id TEXT,
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
    UNIQUE (user_id, folder_id)
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

-- Org isolation: users see only their org's data
CREATE POLICY org_isolation ON organizations
    FOR ALL USING (
        id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY org_isolation ON users
    FOR SELECT USING (true);

CREATE POLICY org_isolation ON users
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY org_isolation ON folders
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
    );

CREATE POLICY org_isolation ON files
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
    );

CREATE POLICY org_isolation ON file_versions
    FOR ALL USING (
        file_id IN (
            SELECT f.id FROM files f
            JOIN users u ON f.org_id = u.org_id
            WHERE u.id = current_setting('app.user_id')::UUID
        )
    );

CREATE POLICY org_isolation ON permissions
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
    );

CREATE POLICY org_isolation ON audit_logs
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
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
