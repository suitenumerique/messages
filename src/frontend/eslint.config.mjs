import { defineConfig, globalIgnores } from "eslint/config";
import i18next from "eslint-plugin-i18next";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const eslintConfig = defineConfig([
  globalIgnores(["dist/**", "src/routes.gen.ts", "scripts/**", '.vite']),
  { ignores: ["src/features/api/gen/**/*.ts", 'public/pdf.worker.min.mjs'] },
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  i18next.configs["flat/recommended"],
  {
    rules: {
      "no-console": ["error", { allow: ["error", "warn"] }],
      "@typescript-eslint/no-unused-vars": "error",
      "@typescript-eslint/no-empty-object-type": "off",
      "react-hooks/exhaustive-deps": "off",
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "warn",
      // Guard against hardcoded user-facing strings. `warn` keeps it
      // non-blocking for now; default `jsx-text-only` mode flags visible JSX
      // text while leaving technical attributes (className, type…) alone.
      "i18next/no-literal-string": "warn",
    },
  },
]);

export default eslintConfig;
