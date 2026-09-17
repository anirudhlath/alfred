import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  {
    // The service workers in public/. Not TypeScript — they are shipped verbatim
    // by Vite's public/ copy, are loaded as classic (non-module) workers, and
    // are evaluated in tests with `new Function("self", source)`, which refuses
    // import/export. `sourceType: 'script'` is what keeps all three honest.
    files: ['public/*.js'],
    extends: [js.configs.recommended],
    languageOptions: {
      globals: globals.serviceworker,
      ecmaVersion: 2023,
      sourceType: 'script',
    },
    rules: {
      // Rule 3 of sw.js's header: every global goes through `self.`, because
      // that object is the entire world src/test/sw-harness.ts can replace.
      // `no-undef` cannot enforce it — `globals.serviceworker` makes every one
      // of these *defined*, so a single bare `caches.open(…)` would lint clean
      // and quietly escape the fake scope. This is the only thing that says so.
      'no-restricted-globals': [
        'error',
        // Kept in step with SEALED in src/test/sw-harness.ts, which shadows the
        // same names at run time.
        ...[
          'AbortController',
          'addEventListener',
          'caches',
          'clearTimeout',
          'clients',
          'fetch',
          'location',
          'registration',
          'setTimeout',
          'skipWaiting',
        ].map((name) => ({
          name,
          message: `Reach it through self.${name} — the tests replace the worker's scope through \`self\`.`,
        })),
      ],
    },
  },
])
