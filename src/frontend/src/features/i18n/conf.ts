import { APP_STORAGE_PREFIX } from "../config/constants";

export const LANGUAGE_LOCAL_STORAGE = APP_STORAGE_PREFIX + 'language';

// Locales handled by the i18next-cli extraction (build time; the other
// `public/locales` files are managed through Crowdin). The list of languages
// enabled at runtime comes from the backend `/config` endpoint.
export const SUPPORTED_LOCALES = ["en-US", "fr-FR", "nl-NL"];
