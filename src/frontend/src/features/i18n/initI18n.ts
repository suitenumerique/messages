import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import HttpApiBackend from "i18next-http-backend";

import { LANGUAGES_ALLOWED, LANGUAGE_LOCAL_STORAGE } from "./conf";
import { getLanguage } from "./utils";

i18n
  .use(initReactI18next)
  .use(HttpApiBackend)
  .init({
    lng: getLanguage(),
    supportedLngs: LANGUAGES_ALLOWED,
    // We register two namespaces
    // - common: for the common strings
    // - roles: for the roles strings as they cannot be extracted by i18next-cli as key are dynamic
    ns: ["common", "roles"],
    defaultNS: "common",
    // Use flat keys and avoid interpreting ':' or '.' in natural language keys
    keySeparator: false,
    nsSeparator: false,
    interpolation: {
      escapeValue: false,
    },
    preload: LANGUAGES_ALLOWED,
    fallbackLng: 'en-US',
    // Consider empty strings as missing keys to fallback to the key
    returnEmptyString: false,
    backend: {
      loadPath: "/locales/{{ns}}/{{lng}}.json",
    }
  })
  .catch(() => {
    throw new Error("i18n initialization failed");
  });

// Save language in local storage
i18n.on("languageChanged", (lng) => {
  if (typeof window !== "undefined") {
    document.documentElement.setAttribute("lang", lng);
    localStorage.setItem(LANGUAGE_LOCAL_STORAGE, lng);
  }
});

export default i18n;
