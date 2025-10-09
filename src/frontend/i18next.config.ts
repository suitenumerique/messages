import { defineConfig } from 'i18next-cli';
import { LANGUAGES_ALLOWED } from './src/features/i18n/conf';

export default defineConfig({
  locales: LANGUAGES_ALLOWED,
  extract: {
    defaultNS: "common",
    input: ['src/**/*.{js,jsx,ts,tsx}'],
    output: "public/locales/{{namespace}}/{{language}}.json",
    // Use flat keys so natural-language strings can be used as full keys without nesting
    keySeparator: false,
    // Avoid splitting ns from key when authors include ':' in text
    nsSeparator: false,
    primaryLanguage: "en-US",
    functions: ['t', 'i18n.t'],
  }
});
