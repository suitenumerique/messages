/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly NEXT_PUBLIC_API_ORIGIN?: string;
  /** @deprecated use the FRONTEND_THEME_CONFIG backend setting */
  readonly NEXT_PUBLIC_THEME_CONFIG?: string;
  /** @deprecated use the LANGUAGES backend setting */
  readonly NEXT_PUBLIC_LANGUAGES?: string;
  /** @deprecated use the LANGUAGE_CODE backend setting */
  readonly NEXT_PUBLIC_DEFAULT_LANGUAGE?: string;
  /** @deprecated use the FRONTEND_FORCED_DEFAULT_LANGUAGE backend setting */
  readonly NEXT_PUBLIC_FORCED_DEFAULT_LANGUAGE?: string;
  /** @deprecated use the FRONTEND_FEEDBACK_WIDGET_CONFIG backend setting */
  readonly NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL?: string;
  /** @deprecated use the FRONTEND_FEEDBACK_WIDGET_CONFIG backend setting */
  readonly NEXT_PUBLIC_FEEDBACK_WIDGET_PATH?: string;
  /** @deprecated use the FRONTEND_FEEDBACK_WIDGET_CONFIG backend setting */
  readonly NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL?: string;
  /** @deprecated use the FRONTEND_FEEDBACK_WIDGET_CONFIG backend setting */
  readonly NEXT_PUBLIC_FEEDBACK_WIDGET_HOME_CHANNEL?: string;
  /** @deprecated use the FRONTEND_HELP_CENTER_URL backend setting */
  readonly NEXT_PUBLIC_HELP_CENTER_URL?: string;
  /** @deprecated use the FRONTEND_LAGAUFRE_WIDGET_CONFIG backend setting */
  readonly NEXT_PUBLIC_LAGAUFRE_WIDGET_API_URL?: string;
  /** @deprecated use the FRONTEND_LAGAUFRE_WIDGET_CONFIG backend setting */
  readonly NEXT_PUBLIC_LAGAUFRE_WIDGET_PATH?: string;
  /** @deprecated use the FRONTEND_MULTIPART_UPLOAD_CHUNK_SIZE_MB backend setting */
  readonly NEXT_PUBLIC_MULTIPART_UPLOAD_CHUNK_SIZE?: string;
  /** @deprecated use the SENTRY_DSN backend setting */
  readonly NEXT_PUBLIC_SENTRY_DSN?: string;
  /** @deprecated the frontend now uses the backend ENVIRONMENT */
  readonly NEXT_PUBLIC_SENTRY_ENVIRONMENT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
