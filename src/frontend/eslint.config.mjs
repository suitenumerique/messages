import { defineConfig, globalIgnores } from "eslint/config";
import i18next from "eslint-plugin-i18next";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const eslintConfig = defineConfig([
  globalIgnores(["dist/**", "src/routes.gen.ts", "scripts/**", '.vite', "android/**", "ios/**"]),
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
  {
    // Email addresses go through MailHelper. Splitting on '@' by hand takes
    // the first separator, which mangles a quoted local part; toLowerCase()
    // folds non-ASCII code points onto ASCII (U+212A KELVIN SIGN becomes
    // 'k'), silently merging addresses that are not the same.
    //
    // mail-helper.tsx implements the policy, so it is the one file allowed
    // the raw operations.
    ignores: ["src/features/utils/mail-helper.tsx"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "CallExpression[callee.property.name='split'][arguments.0.value='@']",
          message:
            "Hand-rolled address split on '@'. Use MailHelper.splitEmail or MailHelper.getDomainFromEmail.",
        },
        {
          selector:
            "CallExpression[callee.property.name=/^(toLowerCase|toLocaleLowerCase|toUpperCase|toLocaleUpperCase)$/][callee.object.name=/(email|addr|domain|sender|recipient)/i]",
          message:
            "Unicode case fold on an address. Use MailHelper.asciiLower, or MailHelper.normalizeEmailDomain for a domain.",
        },
        {
          selector:
            "CallExpression[callee.property.name=/^(toLowerCase|toLocaleLowerCase|toUpperCase|toLocaleUpperCase)$/][callee.object.property.name=/(email|addr|domain|sender|recipient)/i]",
          message:
            "Unicode case fold on an address. Use MailHelper.asciiLower, or MailHelper.normalizeEmailDomain for a domain.",
        },
      ],
    },
  },
]);

export default eslintConfig;
