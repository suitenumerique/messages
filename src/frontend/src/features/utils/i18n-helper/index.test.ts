import { describe, expect, it } from "vitest";
import { I18nHelper } from "./index";

describe("I18nHelper.pickTranslation", () => {
  const translations = {
    "en-us": "Maintenance tonight",
    "fr-fr": "Maintenance ce soir",
  };

  it("picks the translation matching the language case-insensitively", () => {
    expect(I18nHelper.pickTranslation(translations, "fr-FR")).toBe("Maintenance ce soir");
  });

  it("falls back on the same base language", () => {
    expect(I18nHelper.pickTranslation(translations, "fr-CA")).toBe("Maintenance ce soir");
    expect(I18nHelper.pickTranslation(translations, "fr")).toBe("Maintenance ce soir");
  });

  it("falls back on the fallback language", () => {
    expect(I18nHelper.pickTranslation(translations, "nl-NL", "en-US")).toBe("Maintenance tonight");
  });

  it("falls back on the base language of the fallback language", () => {
    expect(I18nHelper.pickTranslation(translations, "nl-NL", "fr")).toBe("Maintenance ce soir");
  });

  it("falls back on any translation", () => {
    expect(I18nHelper.pickTranslation({ "fr-fr": "Maintenance ce soir" }, "nl-NL", "en-US")).toBe("Maintenance ce soir");
  });

  it("ignores blank translations", () => {
    expect(I18nHelper.pickTranslation({ "fr-fr": " ", "en-us": "Maintenance tonight" }, "fr-FR")).toBe("Maintenance tonight");
  });

  it("returns undefined without translation", () => {
    expect(I18nHelper.pickTranslation({}, "fr-FR", "en-US")).toBeUndefined();
  });
});
