import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { keycloakify } from "keycloakify/vite-plugin";

export default defineConfig({
  plugins: [
    react(),
    keycloakify({
      themeName: "lst-dsfr",
      accountThemeImplementation: "Single-Page",
      extraThemeProperties: ["darkMode=false"],
      environmentVariables: [
        { name: "DSFR_THEME_HOME_URL", default: "/" },
        { name: "DSFR_THEME_SERVICE_TITLE", default: "Messages" },
        { name: "DSFR_THEME_BRAND_TOP", default: "République<br/>Française" },
        { name: "DSFR_THEME_HIDE_LOGOUT_BUTTON", default: "false" },
        { name: "DSFR_THEME_TWO_FACTOR_HELP_URL", default: "" },
        { name: "DSFR_THEME_FORGOT_PASSWORD_URL", default: "" },
      ],
    }),
  ],
});
