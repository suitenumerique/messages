import { LANGUAGE_LOCAL_STORAGE } from './conf';

export const getLanguage = (
  languagesAllowed: string[],
  baseLanguage: string,
  isLanguageForced: boolean,
) => {
  if (typeof window === 'undefined') {
    return baseLanguage;
  }

  const storedLanguage = localStorage.getItem(LANGUAGE_LOCAL_STORAGE);
  const languageStore =
    storedLanguage || (isLanguageForced ? baseLanguage : navigator?.language);

  return languagesAllowed.includes(languageStore) ? languageStore : baseLanguage;
};
