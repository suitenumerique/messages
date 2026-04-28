import {
  BASE_LANGUAGE,
  IS_LANGUAGE_FORCED,
  LANGUAGES_ALLOWED,
  LANGUAGE_LOCAL_STORAGE,
} from './conf';

export const getLanguage = () => {
  if (typeof window === 'undefined') {
    return BASE_LANGUAGE;
  }

  const storedLanguage = localStorage.getItem(LANGUAGE_LOCAL_STORAGE);
  const languageStore =
    storedLanguage || (IS_LANGUAGE_FORCED ? BASE_LANGUAGE : navigator?.language);

  return LANGUAGES_ALLOWED.includes(languageStore) ? languageStore : BASE_LANGUAGE;
};
