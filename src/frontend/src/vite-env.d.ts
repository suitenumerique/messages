/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly NEXT_PUBLIC_API_ORIGIN?: string;
  readonly NEXT_PUBLIC_THEME_CONFIG?: string;
  readonly NEXT_PUBLIC_LANGUAGES?: string;
  readonly NEXT_PUBLIC_DEFAULT_LANGUAGE?: string;
  readonly NEXT_PUBLIC_FORCED_DEFAULT_LANGUAGE?: string;
  readonly NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL?: string;
  readonly NEXT_PUBLIC_FEEDBACK_WIDGET_PATH?: string;
  readonly NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL?: string;
  readonly NEXT_PUBLIC_FEEDBACK_WIDGET_HOME_CHANNEL?: string;
  readonly NEXT_PUBLIC_HELP_CENTER_URL?: string;
  readonly NEXT_PUBLIC_LAGAUFRE_WIDGET_API_URL?: string;
  readonly NEXT_PUBLIC_LAGAUFRE_WIDGET_PATH?: string;
  readonly NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE?: string;
  readonly NEXT_PUBLIC_SENTRY_DSN?: string;
  readonly NEXT_PUBLIC_SENTRY_ENVIRONMENT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
