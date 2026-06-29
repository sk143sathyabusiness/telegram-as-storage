CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    industry TEXT,
    size TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    settings JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'read_only' CHECK (role IN ('master_admin', 'org_admin', 'read_write', 'read_only')),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- File versioning and storage tables with org scoping
CREATE TABLE folders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES folders(id),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    folder_id UUID REFERENCES folders(id),
    name TEXT NOT NULL,
    mime_type TEXT,
    size BIGINT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE file_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID REFERENCES files(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now(),
    checksum TEXT,
    UNIQUE (file_id, version_number)
);

CREATE TABLE file_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_version_id UUID REFERENCES file_versions(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_data BYTEA NOT NULL,
    UNIQUE (file_version_id, chunk_index)
);

CREATE TABLE logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    action TEXT NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Enable Row Level Security (RLS)
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE files ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE logs ENABLE ROW LEVEL SECURITY;

-- RLS Policies for organizations
CREATE POLICY "Organizations policy for authenticated users"
    ON organizations
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- RLS Policies for users
CREATE POLICY "Users policy for organization members"
    ON users
    FOR SELECT
    USING (true);

CREATE POLICY "Users policy for organization owners"
    ON users
    FOR ALL
    USING (true)
    WITH CHECK (true);

-- RLS Policies for folders
CREATE POLICY "Folders policy for organization members"
    ON folders
    FOR ALL
    USING (
        org_id IN (
            SELECT org_id FROM users WHERE id = current_setting('app.user_id')::uuid
        )
    );

-- RLS Policies for files
CREATE POLICY "Files policy for organization members"
    ON files
    FOR ALL
    USING (
        org_id IN (
            SELECT org_id FROM users WHERE id = current_setting('app.user_id')::uuid
        )
    );

-- RLS Policies for file versions
CREATE POLICY "File versions policy for organization members"
    ON file_versions
    FOR ALL
    USING (
        file_id IN (
            SELECT f.id FROM files f
            JOIN users u ON f.org_id = u.org_id
            WHERE u.id = current_setting('app.user_id')::uuid
        )
    );

-- RLS Policies for file chunks
CREATE POLICY "File chunks policy for organization members"
    ON file_chunks
    FOR ALL
    USING (
        file_version_id IN (
            SELECT fv.id FROM file_versions fv
            JOIN files f ON fv.file_id = f.id
            JOIN users u ON f.org_id = u.org_id
            WHERE u.id = current_setting('app.user_id')::uuid
        )
    );

-- RLS Policies for logs
CREATE POLICY "Logs policy for organization members"
    ON logs
    FOR ALL
    USING (
        org_id IN (
            SELECT org_id FROM users WHERE id = current_setting('app.user_id')::uuid
        )
    );

-- Master admin bypass policy - allows master_admin to access all organizations
CREATE POLICY "Master admin bypass"
    ON users
    FOR SELECT
    USING (role = 'master_admin');

-- Helper function to set user ID context
CREATE OR REPLACE FUNCTION set_app_user_id(uid UUID)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM set_config('app.user_id', uid::text, true);
END;
$$;

-- Enable security for the helper function
GRANT EXECUTE ON FUNCTION set_app_user_id TO authenticated;