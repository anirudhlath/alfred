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
    // The two service workers. Not TypeScript — they are shipped verbatim by
    // Vite's public/ copy, are loaded as classic (non-module) workers, and are
    // evaluated in tests with `new Function("self", source)`, which refuses
    // import/export. `sourceType: 'script'` is what keeps all three honest.
    files: ['public/*.js'],
    extends: [js.configs.recommended],
    languageOptions: {
      globals: globals.serviceworker,
      ecmaVersion: 2023,
      sourceType: 'script',
    },
  },
])
