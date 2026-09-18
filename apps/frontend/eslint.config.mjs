import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({
  baseDirectory: import.meta.dirname,
});

// Node globals used by the e2e harness scripts (mock IdP, reverse proxy, setup).
const nodeGlobals = {
  console: "readonly",
  process: "readonly",
  Buffer: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  Map: "readonly",
  Set: "readonly",
  Promise: "readonly",
  setTimeout: "readonly",
  clearTimeout: "readonly",
  fetch: "readonly",
  Response: "readonly",
};

const eslintConfig = [
  {
    ignores: [".next/**", "node_modules/**", "out/**", "coverage/**", "dist/**", "next-env.d.ts"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    files: ["e2e/**/*.mjs", "playwright.config.mjs"],
    languageOptions: {
      globals: nodeGlobals,
    },
  },
];

export default eslintConfig;
