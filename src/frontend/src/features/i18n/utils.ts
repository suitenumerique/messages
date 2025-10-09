import {
  BASE_LANGUAGE,
  LANGUAGES_ALLOWED,
  LANGUAGE_LOCAL_STORAGE,
} from './conf';

export const getLanguage = () => {
  if (typeof window === 'undefined') {
    return BASE_LANGUAGE;
  }

  const languageStore =
    localStorage.getItem(LANGUAGE_LOCAL_STORAGE) || navigator?.language;

  return LANGUAGES_ALLOWED.includes(languageStore) ? languageStore : BASE_LANGUAGE;
};
