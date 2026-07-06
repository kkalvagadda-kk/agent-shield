import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Component/unit test config (Vitest + React Testing Library).
// Separate from vite.config.ts so the dev-server proxy doesn't leak into tests.
// E2E (Playwright) lives under e2e/ and is excluded here.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**"],
    css: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.{test,spec}.{ts,tsx}", "src/test/**", "src/main.tsx"],
    },
  },
});
