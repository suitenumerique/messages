import { APP_STORAGE_PREFIX } from "../config/constants";

export const LANGUAGES = JSON.parse(process.env.NEXT_PUBLIC_LANGUAGES || '[["en-US","English"],["fr-FR","Français"]]');
export const LANGUAGES_ALLOWED = LANGUAGES.map((language: [string, string]) => language[0]);
export const LANGUAGE_LOCAL_STORAGE = APP_STORAGE_PREFIX + 'language';
export const BASE_LANGUAGE = process.env.NEXT_PUBLIC_DEFAULT_LANGUAGE || LANGUAGES_ALLOWED[0];
