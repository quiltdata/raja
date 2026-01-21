-- Cedar / AVP Authorization Schema (Materialized)
--
-- This schema implements a three-level, materialized hierarchy:
--   PackageGrant → PathRule → AVPPolicy
--
-- All levels are explicitly stored for auditability, debuggability,
-- and deterministic publishing. No virtual rules or runtime expansion.

-- =============================================================================
-- 1. Package Grants (Intent Layer)
-- =============================================================================

CREATE TABLE package_grants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    package_id UUID NOT NULL,
    role_id UUID NOT NULL,

    -- Access mode: read | readwrite
    mode VARCHAR(20) NOT NULL CHECK (mode IN ('read', 'readwrite')),

    -- Enforcement strategy:
    --   prefix_envelope: Access granted to entire bucket/prefix envelope
    --   manifest_enforced: Access restricted to specific files listed in manifest
    enforcement VARCHAR(30) NOT NULL CHECK (enforcement IN ('prefix_envelope', 'manifest_enforced')),

    -- Soft delete / enable flag
    enabled BOOLEAN NOT NULL DEFAULT true,

    -- Audit fields
    created_by UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMP WITH TIME ZONE,

    -- Constraints
    FOREIGN KEY (package_id) REFERENCES packages(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

-- Indexes for common queries
CREATE INDEX idx_package_grants_package_id ON package_grants(package_id) WHERE enabled = true;
CREATE INDEX idx_package_grants_role_id ON package_grants(role_id) WHERE enabled = true;
CREATE INDEX idx_package_grants_enabled ON package_grants(enabled);

-- Trigger to update updated_at
CREATE TRIGGER package_grants_updated_at
    BEFORE UPDATE ON package_grants
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 2. Path Rules (Operational Layer)
-- =============================================================================

CREATE TABLE path_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Bucket and path (both required)
    bucket VARCHAR(255) NOT NULL,

    -- Path semantics:
    --   "" (empty string) = root prefix (entire bucket)
    --   trailing "/" = prefix scope (e.g., "incoming/")
    --   no trailing "/" = exact key (e.g., "reports/2024.parquet")
    path TEXT NOT NULL,

    -- Role and access mode
    role_id UUID NOT NULL,
    mode VARCHAR(20) NOT NULL CHECK (mode IN ('read', 'readwrite')),

    -- Origin tracking:
    --   manual: Created manually via Bucket Permissions pane
    --   derived_from_grant: Automatically derived from a Package Grant
    origin VARCHAR(30) NOT NULL CHECK (origin IN ('manual', 'derived_from_grant')),

    -- Link to Package Grant (NULL for manual rules)
    package_grant_id UUID,

    -- Soft delete / enable flag
    enabled BOOLEAN NOT NULL DEFAULT true,

    -- Audit fields
    created_by UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMP WITH TIME ZONE,

    -- Constraints
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE,
    FOREIGN KEY (package_grant_id) REFERENCES package_grants(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id),

    -- Ensure package_grant_id is set if and only if origin is derived_from_grant
    CHECK (
        (origin = 'manual' AND package_grant_id IS NULL) OR
        (origin = 'derived_from_grant' AND package_grant_id IS NOT NULL)
    )
);

-- Indexes for common queries
CREATE INDEX idx_path_rules_bucket ON path_rules(bucket) WHERE enabled = true;
CREATE INDEX idx_path_rules_role_id ON path_rules(role_id) WHERE enabled = true;
CREATE INDEX idx_path_rules_package_grant_id ON path_rules(package_grant_id) WHERE enabled = true;
CREATE INDEX idx_path_rules_enabled ON path_rules(enabled);
CREATE INDEX idx_path_rules_bucket_path ON path_rules(bucket, path) WHERE enabled = true;

-- Trigger to update updated_at
CREATE TRIGGER path_rules_updated_at
    BEFORE UPDATE ON path_rules
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 3. AVP Policies (Compiled Layer)
-- =============================================================================

CREATE TABLE avp_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Link to source Path Rule
    path_rule_id UUID NOT NULL,

    -- AVP policy identifier (deterministic)
    -- Format: quilt:pathrule:<path_rule_id>:<action>
    -- Example: quilt:pathrule:8f12...:s3:GetObject
    policy_id VARCHAR(500) NOT NULL UNIQUE,

    -- S3 action (explicit action, not mode)
    -- Examples: s3:GetObject, s3:ListBucket, s3:PutObject, s3:DeleteObject
    action VARCHAR(100) NOT NULL,

    -- Policy content hash (for reconciliation)
    policy_hash VARCHAR(64) NOT NULL,

    -- Full Cedar policy text (for debugging/audit)
    cedar_policy TEXT NOT NULL,

    -- AVP metadata
    avp_policy_store_id VARCHAR(255) NOT NULL,
    avp_created_at TIMESTAMP WITH TIME ZONE,
    avp_updated_at TIMESTAMP WITH TIME ZONE,

    -- Audit fields
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMP WITH TIME ZONE,

    -- Constraints
    FOREIGN KEY (path_rule_id) REFERENCES path_rules(id) ON DELETE CASCADE,

    -- Ensure policy_id follows deterministic format
    CHECK (policy_id LIKE 'quilt:pathrule:%')
);

-- Indexes for common queries
CREATE INDEX idx_avp_policies_path_rule_id ON avp_policies(path_rule_id);
CREATE INDEX idx_avp_policies_policy_id ON avp_policies(policy_id);
CREATE INDEX idx_avp_policies_action ON avp_policies(action);

-- Trigger to update updated_at
CREATE TRIGGER avp_policies_updated_at
    BEFORE UPDATE ON avp_policies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- Helper Function: Update Timestamp Trigger
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Views for Common Queries
-- =============================================================================

-- View: All active path rules with their source grants (if any)
CREATE VIEW v_active_path_rules AS
SELECT
    pr.id,
    pr.bucket,
    pr.path,
    pr.role_id,
    pr.mode,
    pr.origin,
    pr.package_grant_id,
    pg.package_id,
    pr.created_at,
    pr.created_by
FROM path_rules pr
LEFT JOIN package_grants pg ON pr.package_grant_id = pg.id
WHERE pr.enabled = true;

-- View: All active grants with their derived path rules count
CREATE VIEW v_active_grants_with_rules AS
SELECT
    pg.id AS grant_id,
    pg.package_id,
    pg.role_id,
    pg.mode,
    pg.enforcement,
    COUNT(pr.id) AS derived_rules_count,
    pg.created_at,
    pg.created_by
FROM package_grants pg
LEFT JOIN path_rules pr ON pg.id = pr.package_grant_id AND pr.enabled = true
WHERE pg.enabled = true
GROUP BY pg.id, pg.package_id, pg.role_id, pg.mode, pg.enforcement, pg.created_at, pg.created_by;

-- View: AVP policies with their source path rules
CREATE VIEW v_avp_policies_with_context AS
SELECT
    ap.id AS avp_policy_id,
    ap.policy_id,
    ap.action,
    ap.policy_hash,
    pr.id AS path_rule_id,
    pr.bucket,
    pr.path,
    pr.role_id,
    pr.mode,
    pr.origin,
    pr.package_grant_id,
    ap.created_at
FROM avp_policies ap
JOIN path_rules pr ON ap.path_rule_id = pr.id
WHERE ap.deleted_at IS NULL;

-- =============================================================================
-- Sample Queries
-- =============================================================================

-- Query: Find all path rules for a bucket
-- SELECT * FROM v_active_path_rules WHERE bucket = 'my-bucket';

-- Query: Find all path rules derived from a specific grant
-- SELECT * FROM v_active_path_rules WHERE package_grant_id = '...';

-- Query: Find all manual path rules for a bucket
-- SELECT * FROM v_active_path_rules WHERE bucket = 'my-bucket' AND origin = 'manual';

-- Query: Find all AVP policies for a role
-- SELECT * FROM v_avp_policies_with_context WHERE role_id = '...';

-- Query: Find orphaned AVP policies (policies without active path rules)
-- SELECT ap.* FROM avp_policies ap
-- LEFT JOIN path_rules pr ON ap.path_rule_id = pr.id
-- WHERE pr.id IS NULL OR pr.enabled = false;

-- =============================================================================
-- Invariants and Business Rules
-- =============================================================================

-- Invariant 1: Every AVP policy belongs to exactly one Path Rule
--   Enforced by: FOREIGN KEY (path_rule_id) NOT NULL

-- Invariant 2: Every Path Rule belongs to at most one Package Grant
--   Enforced by: FOREIGN KEY (package_grant_id) nullable

-- Invariant 3: Package Grants never directly generate AVP policies
--   Enforced by: No direct FK between package_grants and avp_policies

-- Invariant 4: The empty path ("") always means root prefix
--   Enforced by: Application logic (path can be empty string)

-- Invariant 5: AVP is never treated as the system of record
--   Enforced by: Postgres is the source of truth; AVP is synchronized

-- Invariant 6: Derived path rules must reference a grant
--   Enforced by: CHECK constraint on origin/package_grant_id relationship

-- =============================================================================
-- Notes on Action Expansion
-- =============================================================================

-- Action Mode Mappings (MVP):
--
-- Read mode expands to:
--   - s3:GetObject (includes HeadObject)
--   - s3:ListBucket (scoped to prefix via Cedar condition)
--
-- ReadWrite mode expands to:
--   - s3:GetObject
--   - s3:ListBucket
--   - s3:PutObject
--   - (future) s3:AbortMultipartUpload
--
-- Note: s3:DeleteObject is intentionally excluded from ReadWrite in MVP

-- =============================================================================
-- Migration Strategy
-- =============================================================================

-- 1. Create tables (package_grants, path_rules, avp_policies)
-- 2. Create helper function (update_updated_at_column)
-- 3. Create triggers (updated_at triggers)
-- 4. Create indexes (for query performance)
-- 5. Create views (for common queries)
-- 6. (Optional) Seed initial data for testing
