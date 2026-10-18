// Local accounts seeded by `global-setup.mjs` for the local-login journeys.
// Shared by the plain-ESM setup and the TypeScript specs, so the credentials
// exist once. CommonJS keeps the same module loadable from both.

/** Shared password of every seeded local account. */
const LOCAL_ACCOUNT_PASSWORD = "Local-Account-1";

/** Seeded local accounts, keyed by the role they carry. */
const LOCAL_ACCOUNTS = {
  admin: { email: "carol@acme.com", name: "Carol", role: "agent_admin" },
  employee: { email: "dave@acme.com", name: "Dave", role: "employee" },
  // The member-management journeys sign in as a platform administrator; no
  // other seeded account carries that role.
  platformAdmin: { email: "root@acme.com", name: "Root", role: "platform_admin" },
  // Reset target: seeded NOT needing a change, so a password reset must flip
  // the flag the member list reports.
  resetTarget: { email: "frank@acme.com", name: "Frank", role: "employee" },
  // Forced-change account: the first-login password journey rotates its
  // password, so it stays off the accounts the other journeys sign in with.
  pending: {
    email: "erin@acme.com",
    name: "Erin",
    role: "employee",
    mustChangePassword: true,
  },
};

module.exports = { LOCAL_ACCOUNT_PASSWORD, LOCAL_ACCOUNTS };
