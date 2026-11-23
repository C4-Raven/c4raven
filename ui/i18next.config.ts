import { defineConfig } from 'i18next-cli';

// English-only catalogue: the extracted keys are the English source strings.
// Separators are off to match src/i18n.ts (keys are full sentences).
export default defineConfig({
  locales: ['en'],
  extract: {
    input: ['src/**/*.{ts,tsx}', '!src/**/*.{test,stories}.{ts,tsx}'],
    output: 'public/locales/{{language}}/{{namespace}}.json',
    defaultNS: 'translation',
    keySeparator: false,
    nsSeparator: false,
    // English needs no plural variants: the key text is shown as-is.
    disablePlurals: true,
    sort: true,
    indentation: 2,
  },
});
