import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import HttpApiBackend from "i18next-http-backend";

import { AppConfig } from "@/features/config/resolve";
import { LANGUAGE_LOCAL_STORAGE } from "./conf";
import { getLanguage } from "./utils";

/**
 * Initialize i18next from the resolved application configuration.
 * Called during bootstrap, before the React tree renders, as components rely
 * on `useTranslation` from the very first render. Locale files load
 * asynchronously; react-i18next re-renders when they arrive.
 */
export const initI18n = (config: AppConfig) => {
  const languagesAllowed = config.LANGUAGES.map(([code]) => code);

  i18n
    .use(initReactI18next)
    .use(HttpApiBackend)
    .init({
      lng: getLanguage(
        languagesAllowed,
        config.BASE_LANGUAGE,
        config.IS_LANGUAGE_FORCED,
      ),
      supportedLngs: languagesAllowed,
      // Register namespaces
      // - common: for the common strings
      // - roles: for the roles strings as they cannot be extracted by i18next-cli as key are dynamic
      // - placeholders: for the built-in template/signature placeholders (dynamic keys, same as roles)
      ns: ["common", "roles", "placeholders"],
      defaultNS: "common",
      // Use flat keys and avoid interpreting ':' or '.' in natural language keys
      keySeparator: false,
      nsSeparator: false,
      interpolation: {
        escapeValue: false,
      },
      preload: languagesAllowed,
      fallbackLng: [config.BASE_LANGUAGE, "en-US"],
      // Consider empty strings as missing keys to fallback to the key
      returnEmptyString: false,
      backend: {
        loadPath: "/locales/{{ns}}/{{lng}}.json",
      }
    })
    .catch((error) => {
      throw new Error("i18n initialization failed", { cause: error });
    });
};

// Save language in local storage
i18n.on("languageChanged", (lng) => {
  if (typeof window !== "undefined") {
    document.documentElement.setAttribute("lang", lng);
    localStorage.setItem(LANGUAGE_LOCAL_STORAGE, lng);
  }
});

export default i18n;
