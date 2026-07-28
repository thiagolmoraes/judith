import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Standalone test config (kept separate from vite.config.ts so the production `vite build` is
// untouched). Reused by later frontend phases — add new `*.test.tsx` files under src/.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    // jsdom needs a non-opaque origin for localStorage; about:blank has none.
    environmentOptions: { jsdom: { url: "http://localhost/" } },
    // Node 25 turned its own `localStorage` global on by default (22.4–24 kept it
    // behind --experimental-webstorage; before 22.4 the flag did not exist). Without --localstorage-file it is a
    // method-less object — and it shadows jsdom's Storage everywhere, so every
    // localStorage call in a test throws "not a function". Seven Sidebar tests failed
    // exactly this way on Node 25 while passing on older Node. Disabling Node's
    // webstorage in the workers lets jsdom's real Storage through.
    pool: "forks",
    poolOptions: { forks: { execArgv: ["--no-experimental-webstorage"] } },
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
