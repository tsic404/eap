-- Seed the identity the mock IdP maps onto. The backend resolves a user's
-- tenant by email domain, so the `acme.com` tenant must exist before the first
-- SSO login; alice is seeded as agent_admin so the admin journeys work.
--
-- Idempotent under concurrency: two overlapping `docker compose up -d` runs are
-- serialized by an advisory lock (the same idiom as app/seed.py), and the
-- inserts conflict on the natural keys (`tenants.sso_domain`,
-- `users(tenant_id, sso_sub)`) instead of check-then-insert — a second
-- `acme.com` tenant would fail every login with TENANT_AMBIGUOUS.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('eap:seed:mock-idp'));

INSERT INTO tenants (id, name, slug, sso_domain, sso_provider, quota_limit, quota_used, status)
SELECT gen_random_uuid(), 'Acme', 'acme', 'acme.com', 'oidc', 10000, 0, 'active'
ON CONFLICT (sso_domain) DO NOTHING;

-- bob stays unseeded on purpose: the backend auto-creates him as employee when
-- a test selects the second mock identity (mock_idp_user cookie).
INSERT INTO users (id, tenant_id, sso_sub, email, name, role, status)
SELECT gen_random_uuid(), t.id, 'user-1', 'alice@acme.com', 'Alice', 'agent_admin', 'active'
FROM tenants t
WHERE t.sso_domain = 'acme.com'
ON CONFLICT (tenant_id, sso_sub) DO NOTHING;

COMMIT;
