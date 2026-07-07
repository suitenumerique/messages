import { ConfigRetrieve200 } from "@/features/api/gen";
import { FooterProps } from "@gouvfr-lasuite/ui-kit";

export type ThemeConfig = {
  theme: "white-label" | "anct" | "dsfr";
  terms_of_service_url?: string;
  footer?: FooterProps;
};

export type FeedbackWidgetConfig = {
  api_url?: string;
  path?: string;
  channel?: string;
  home_channel?: string;
};

export type LagaufreWidgetConfig = {
  api_url?: string;
  path?: string;
};

export type DriveConfig = NonNullable<ConfigRetrieve200["DRIVE"]>;

/**
 * The application configuration exposed to the whole app: the `/config`
 * endpoint payload with frontend-specific keys already resolved (deprecated
 * env var fallbacks applied, languages normalized to BCP 47, widget
 * configurations grouped).
 */
export type AppConfig = Omit<
  ConfigRetrieve200,
  | "DRIVE"
  | "LANGUAGES"
  | "LANGUAGE_CODE"
  | "SENTRY_DSN"
  | "FRONTEND_THEME_CONFIG"
  | "FRONTEND_FORCED_DEFAULT_LANGUAGE"
  | "FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB"
  | "FRONTEND_HELP_CENTER_URL"
  | "FRONTEND_FEEDBACK_WIDGET_CONFIG"
  | "FRONTEND_LAGAUFRE_WIDGET_CONFIG"
> & {
  DRIVE: DriveConfig;
  /** Available languages as (BCP 47 code, label) pairs. */
  LANGUAGES: [string, string][];
  /** Default language, as a BCP 47 code. */
  BASE_LANGUAGE: string;
  /** When true, fall back to BASE_LANGUAGE instead of the browser language. */
  IS_LANGUAGE_FORCED: boolean;
  SENTRY_DSN?: string;
  SENTRY_ENVIRONMENT?: string;
  THEME_CONFIG: ThemeConfig;
  MULTIPART_UPLOAD_CHUNK_SIZE_MB: number;
  HELP_CENTER_URL?: string;
  FEEDBACK_WIDGET: FeedbackWidgetConfig;
  LAGAUFRE_WIDGET: LagaufreWidgetConfig;
};

export const DEFAULT_LANGUAGES: [string, string][] = [
  ["en-US", "English"],
  ["fr-FR", "Français"],
  ["nl-NL", "Nederlands"],
];

const DEFAULT_DRIVE_CONFIG: DriveConfig = {
  sdk_url: "",
  api_url: "",
  file_url: "",
  preview_url: "",
  app_name: "Drive",
};

const DEFAULT_THEME_CONFIG: ThemeConfig = { theme: "white-label" };

const DEFAULT_MULTIPART_UPLOAD_CHUNK_SIZE_MB = 100;

/**
 * Normalize a language code to its BCP 47 casing (`en-us` → `en-US`).
 * The backend exposes Django-style lowercase codes while the locale files
 * and the stored language are BCP 47.
 */
export const toBCP47 = (code: string): string => {
  const [language, region] = code.split("-");
  return region
    ? `${language.toLowerCase()}-${region.toUpperCase()}`
    : language.toLowerCase();
};

const warnedKeys = new Set<string>();

/**
 * Read a deprecated `NEXT_PUBLIC_*` build-time variable as a fallback for a
 * backend-provided setting, warning once per variable. Empty strings are
 * treated as unset, matching how these variables used to behave.
 */
const deprecatedEnv = (
  envKey: string,
  replacement: string,
  raw: string | undefined,
): string | undefined => {
  if (!raw) return undefined;
  if (!warnedKeys.has(envKey)) {
    warnedKeys.add(envKey);
    console.warn(
      `[DEPRECATED] ${envKey} is deprecated and will be removed, ` +
        `configure the backend setting ${replacement} instead.`,
    );
  }
  return raw;
};

const parseJSON = <T>(envKey: string, raw: string | undefined): T | undefined => {
  if (raw === undefined) return undefined;
  try {
    return JSON.parse(raw) as T;
  } catch {
    console.warn(`[config] Ignoring ${envKey}: invalid JSON.`);
    return undefined;
  }
};

const parseIntOrUndefined = (raw: string | undefined): number | undefined => {
  if (raw === undefined) return undefined;
  const value = parseInt(raw, 10);
  return Number.isNaN(value) ? undefined : value;
};

const resolveLanguages = (api?: ConfigRetrieve200): [string, string][] => {
  if (api?.LANGUAGES) {
    return api.LANGUAGES.map(
      ([code, label]): [string, string] => [toBCP47(code), label],
    );
  }
  return (
    parseJSON<[string, string][]>(
      "NEXT_PUBLIC_LANGUAGES",
      deprecatedEnv(
        "NEXT_PUBLIC_LANGUAGES",
        "LANGUAGES",
        import.meta.env.NEXT_PUBLIC_LANGUAGES,
      ),
    ) ?? DEFAULT_LANGUAGES
  );
};

const resolveThemeConfig = (api?: ConfigRetrieve200): ThemeConfig => {
  if (api?.FRONTEND_THEME_CONFIG) {
    return api.FRONTEND_THEME_CONFIG as ThemeConfig;
  }
  return (
    parseJSON<ThemeConfig>(
      "NEXT_PUBLIC_THEME_CONFIG",
      deprecatedEnv(
        "NEXT_PUBLIC_THEME_CONFIG",
        "FRONTEND_THEME_CONFIG",
        import.meta.env.NEXT_PUBLIC_THEME_CONFIG,
      ),
    ) ?? DEFAULT_THEME_CONFIG
  );
};

const resolveFeedbackWidget = (api?: ConfigRetrieve200): FeedbackWidgetConfig => {
  if (api?.FRONTEND_FEEDBACK_WIDGET_CONFIG) {
    return api.FRONTEND_FEEDBACK_WIDGET_CONFIG as FeedbackWidgetConfig;
  }
  return {
    api_url: deprecatedEnv(
      "NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL",
      "FRONTEND_FEEDBACK_WIDGET_CONFIG",
      import.meta.env.NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL,
    ),
    path: deprecatedEnv(
      "NEXT_PUBLIC_FEEDBACK_WIDGET_PATH",
      "FRONTEND_FEEDBACK_WIDGET_CONFIG",
      import.meta.env.NEXT_PUBLIC_FEEDBACK_WIDGET_PATH,
    ),
    channel: deprecatedEnv(
      "NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL",
      "FRONTEND_FEEDBACK_WIDGET_CONFIG",
      import.meta.env.NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL,
    ),
    home_channel: deprecatedEnv(
      "NEXT_PUBLIC_FEEDBACK_WIDGET_HOME_CHANNEL",
      "FRONTEND_FEEDBACK_WIDGET_CONFIG",
      import.meta.env.NEXT_PUBLIC_FEEDBACK_WIDGET_HOME_CHANNEL,
    ),
  };
};

const resolveLagaufreWidget = (api?: ConfigRetrieve200): LagaufreWidgetConfig => {
  if (api?.FRONTEND_LAGAUFRE_WIDGET_CONFIG) {
    return api.FRONTEND_LAGAUFRE_WIDGET_CONFIG as LagaufreWidgetConfig;
  }
  return {
    api_url: deprecatedEnv(
      "NEXT_PUBLIC_LAGAUFRE_WIDGET_API_URL",
      "FRONTEND_LAGAUFRE_WIDGET_CONFIG",
      import.meta.env.NEXT_PUBLIC_LAGAUFRE_WIDGET_API_URL,
    ),
    path: deprecatedEnv(
      "NEXT_PUBLIC_LAGAUFRE_WIDGET_PATH",
      "FRONTEND_LAGAUFRE_WIDGET_CONFIG",
      import.meta.env.NEXT_PUBLIC_LAGAUFRE_WIDGET_PATH,
    ),
  };
};

/**
 * Build the application configuration from the `/config` endpoint payload.
 * Every key resolves as: backend value → deprecated `NEXT_PUBLIC_*` env var
 * (transition fallback) → hardcoded default, so the app can still boot when
 * the backend is unreachable.
 */
export const resolveConfig = (api?: ConfigRetrieve200): AppConfig => {
  const languages = resolveLanguages(api);
  const baseLanguage = api?.LANGUAGE_CODE
    ? toBCP47(api.LANGUAGE_CODE)
    : (deprecatedEnv(
        "NEXT_PUBLIC_DEFAULT_LANGUAGE",
        "LANGUAGE_CODE",
        import.meta.env.NEXT_PUBLIC_DEFAULT_LANGUAGE,
      ) ?? languages[0][0]);

  return {
    ENVIRONMENT: api?.ENVIRONMENT ?? "",
    RELEASE: api?.RELEASE ?? "NA",
    AI_ENABLED: api?.AI_ENABLED ?? false,
    FEATURE_AI_SUMMARY: api?.FEATURE_AI_SUMMARY ?? false,
    FEATURE_AI_AUTOLABELS: api?.FEATURE_AI_AUTOLABELS ?? false,
    FEATURE_MAILBOX_ADMIN_CHANNELS: api?.FEATURE_MAILBOX_ADMIN_CHANNELS ?? [],
    SCHEMA_CUSTOM_ATTRIBUTES_USER: api?.SCHEMA_CUSTOM_ATTRIBUTES_USER ?? {},
    SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN:
      api?.SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN ?? {},
    MAX_OUTGOING_ATTACHMENT_SIZE: api?.MAX_OUTGOING_ATTACHMENT_SIZE ?? 20 * 1024 ** 2,
    MAX_RECIPIENTS_PER_MESSAGE: api?.MAX_RECIPIENTS_PER_MESSAGE ?? 500,
    MAX_TEMPLATE_IMAGE_SIZE: api?.MAX_TEMPLATE_IMAGE_SIZE ?? 2 * 1024 ** 2,
    IMAGE_PROXY_ENABLED: api?.IMAGE_PROXY_ENABLED ?? false,
    FEATURE_MAILDOMAIN_CREATE: api?.FEATURE_MAILDOMAIN_CREATE ?? true,
    FEATURE_MAILDOMAIN_MANAGE_ACCESSES:
      api?.FEATURE_MAILDOMAIN_MANAGE_ACCESSES ?? true,
    FEATURE_MAILDOMAIN_MANAGE_TOTP: api?.FEATURE_MAILDOMAIN_MANAGE_TOTP ?? false,
    FEATURE_THREAD_SPLIT: api?.FEATURE_THREAD_SPLIT ?? true,
    MESSAGES_MANUAL_RETRY_MAX_AGE: api?.MESSAGES_MANUAL_RETRY_MAX_AGE ?? 7 * 24 * 60 ** 2,
    FRONTEND_SILENT_LOGIN_ENABLED: api?.FRONTEND_SILENT_LOGIN_ENABLED ?? false,
    DRIVE: api?.DRIVE ?? DEFAULT_DRIVE_CONFIG,
    LANGUAGES: languages,
    BASE_LANGUAGE: baseLanguage,
    IS_LANGUAGE_FORCED:
      api?.FRONTEND_FORCED_DEFAULT_LANGUAGE ??
      deprecatedEnv(
        "NEXT_PUBLIC_FORCED_DEFAULT_LANGUAGE",
        "FRONTEND_FORCED_DEFAULT_LANGUAGE",
        import.meta.env.NEXT_PUBLIC_FORCED_DEFAULT_LANGUAGE,
      ) === "true",
    SENTRY_DSN:
      api?.SENTRY_DSN ??
      deprecatedEnv(
        "NEXT_PUBLIC_SENTRY_DSN",
        "SENTRY_DSN",
        import.meta.env.NEXT_PUBLIC_SENTRY_DSN,
      ),
    SENTRY_ENVIRONMENT:
      api?.ENVIRONMENT ??
      deprecatedEnv(
        "NEXT_PUBLIC_SENTRY_ENVIRONMENT",
        "ENVIRONMENT (backend)",
        import.meta.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT,
      ),
    THEME_CONFIG: resolveThemeConfig(api),
    MULTIPART_UPLOAD_CHUNK_SIZE_MB:
      api?.FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB ??
      parseIntOrUndefined(
        deprecatedEnv(
          "NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE",
          "FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB",
          import.meta.env.NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE,
        ),
      ) ??
      DEFAULT_MULTIPART_UPLOAD_CHUNK_SIZE_MB,
    HELP_CENTER_URL:
      api?.FRONTEND_HELP_CENTER_URL ??
      deprecatedEnv(
        "NEXT_PUBLIC_HELP_CENTER_URL",
        "FRONTEND_HELP_CENTER_URL",
        import.meta.env.NEXT_PUBLIC_HELP_CENTER_URL,
      ),
    FEEDBACK_WIDGET: resolveFeedbackWidget(api),
    LAGAUFRE_WIDGET: resolveLagaufreWidget(api),
  };
};
