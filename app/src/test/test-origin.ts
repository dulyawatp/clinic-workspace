/**
 * Origin every test-only relative URL resolves against. Kept in sync with the
 * jsdom `url` in `app/vite.config.ts`, so MSW resolves relative `fetch` input
 * to this origin instead of jsdom's default `http://localhost:3000`.
 */
export const TEST_APP_ORIGIN = "http://localhost:5173";
