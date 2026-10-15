import eslint from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["**/dist/**", "**/node_modules/**", "packages/api-client/src/schema.gen.ts"],
  },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-floating-promises": "error",
      "react-refresh/only-export-components": ["warn", { "allowConstantExport": true }]
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh
    }
  }
);

