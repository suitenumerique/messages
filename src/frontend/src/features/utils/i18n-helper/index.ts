export class I18nHelper {
  /**
   * Pick the text matching a language from a set of translations keyed by
   * language code. Codes are compared case-insensitively as the backend uses
   * Django-style codes (`fr-fr`) while the frontend uses BCP 47 (`fr-FR`).
   *
   * Falls back on the same base language (`fr`), then on the fallback
   * language (exact code, then base language), then on any available
   * translation.
   *
   * @param translations - Texts keyed by language code
   * @param language - The language to pick
   * @param fallbackLanguage - The language to pick when `language` is missing
   * @returns The picked text, or undefined when there is no translation
   */
  public static pickTranslation(
    translations: Record<string, string>,
    language: string,
    fallbackLanguage?: string,
  ): string | undefined {
    const entries = Object.entries(translations)
      .filter(([, text]) => text.trim())
      .map(([code, text]): [string, string] => [code.toLowerCase(), text]);
    const findByCode = (code: string) =>
      entries.find(([entryCode]) => entryCode === code.toLowerCase())?.[1];
    const findByBaseLanguage = (code: string) => {
      const baseLanguage = code.toLowerCase().split("-")[0];
      return entries.find(([entryCode]) => entryCode.split("-")[0] === baseLanguage)?.[1];
    };

    return (
      findByCode(language) ??
      findByBaseLanguage(language) ??
      (fallbackLanguage
        ? findByCode(fallbackLanguage) ?? findByBaseLanguage(fallbackLanguage)
        : undefined) ??
      entries[0]?.[1]
    );
  }
}
