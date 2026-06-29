import { useMemo } from "react";
import { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { usePlaceholdersRetrieve } from "@/features/api/gen";

/**
 * Label metadata returned by the placeholders endpoint for a single field.
 *
 * Built-in fields come as an empty object and are localized client-side from
 * the "placeholders" i18next namespace. Custom (instance-defined) attributes
 * carry their schema `title` and, when available, the `x-i18n` translations.
 */
export type PlaceholderFieldMeta = {
  title?: string;
  i18n?: Record<string, string>;
};

export type PlaceholderVariable = {
  value: string;
  label: string;
};

/**
 * Resolve the display label of a placeholder field.
 *
 * @param value - The field slug (e.g. "name", "job_title").
 * @param meta - The label metadata returned by the backend.
 * @param t - The translation function bound to the "placeholders" namespace.
 * @param language - The active language code (may be regional, e.g. "fr-FR").
 * @returns The localized label, falling back to the slug.
 */
export const resolvePlaceholderLabel = (
  value: string,
  meta: PlaceholderFieldMeta,
  t: TFunction,
  language: string,
): string => {
  // Instance-defined attribute with translations: pick the active language.
  if (meta?.i18n) {
    // Fall back to the base language ("fr-FR" -> "fr") since the backend i18n
    // map may only expose base codes.
    const baseLanguage = language.split("-")[0];
    return (
      meta.i18n[language] ??
      meta.i18n[baseLanguage] ??
      meta.i18n.en ??
      meta.title ??
      value
    );
  }
  // Instance-defined attribute without translations: use its schema title.
  if (meta?.title) {
    return meta.title;
  }
  // Built-in placeholder: translated client-side from the "placeholders" namespace.
  return t(value, { ns: "placeholders", defaultValue: value });
};

/**
 * Fetch the available template/signature variables with localized labels.
 *
 * @param enabled - Whether the underlying query should run.
 * @returns The resolved variables and the loading state.
 */
export const usePlaceholderVariables = (
  enabled: boolean = true,
): { variables: PlaceholderVariable[]; isLoading: boolean } => {
  const { t, i18n } = useTranslation();
  const { data: { data: placeholders } = {}, isLoading } = usePlaceholdersRetrieve({
    query: {
      enabled,
      refetchOnMount: true,
      refetchOnWindowFocus: true,
    },
  });

  const language = i18n.language;
  const variables = useMemo<PlaceholderVariable[]>(() => {
    if (!placeholders) return [];
    return Object.entries(placeholders).map(([value, meta]) => ({
      value,
      label: resolvePlaceholderLabel(value, meta, t, language),
    }));
  }, [placeholders, t, language]);

  return { variables, isLoading };
};
