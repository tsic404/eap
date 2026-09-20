-- Seed the tenant the mock IdP's user (alice@acme.com) maps onto. The backend
-- resolves a user's tenant by email domain, so this tenant must exist before
-- the browser SSO flow runs. Idempotent: re-running `make qa-up` never creates
-- a duplicate row.
INSERT INTO tenants (id, name, slug, sso_domain, sso_provider, quota_limit, quota_used, status)
SELECT gen_random_uuid(), 'Acme', 'acme-e2e', 'acme.com', 'oidc', 10000, 0, 'active'
WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE sso_domain = 'acme.com');
