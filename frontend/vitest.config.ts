import { defineConfig } from "vitest/config";
import tsConfigPaths from "vite-tsconfig-paths";

// Deliberately its own minimal config, not a re-export of vite.config.ts —
// that file pulls in the TanStack Start/Nitro/Tailwind plugin chain, none of
// which unit tests for plain TypeScript logic (this plan's actual target)
// need or benefit from. Keeping the two configs separate means a change to
// the app's build plugins can never accidentally break the test runner.
export default defineConfig({
  plugins: [tsConfigPaths({ projects: ["./tsconfig.json"] })],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
