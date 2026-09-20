import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // Cap workers: the default (numCpus - 1) forks oversubscribe under high load, timing out worker startup and dropping all tests.
    maxWorkers: 4,
    exclude: ["**/node_modules/**", "e2e/**"],
  },
});
