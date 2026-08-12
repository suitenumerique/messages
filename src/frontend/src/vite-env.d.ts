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
  /** @deprecated use the MOBILE_OTA_MANIFEST_URL backend setting */
  readonly NEXT_PUBLIC_MOBILE_OTA_MANIFEST_URL?: string;
  // Mobile hot reload: Vite dev server URL baked as the WebView's server.url
  // (capacitor.config.ts). Exposed so ota.ts can skip OTA during such a session.
  readonly MOBILE_DEV_SERVER_URL?: string;
  // OTA bundle-verification public key, also baked natively at `cap sync`
  // (capacitor.config.ts). Exposed so ota.ts can refuse a server-provided
  // manifest URL on a build that embeds no key.
  readonly MOBILE_OTA_SIGNING_PUBLIC_KEY_B64?: string;
  // OIDC deep-link scheme, per environment so two builds can coexist on a
  // device. Also declared natively (Android manifestPlaceholder, iOS build
  // setting) from the same variable — see auth.ts.
  readonly MOBILE_AUTH_SCHEME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Per-build version stamp injected by Vite (see vite.config.ts `define`). Used
// to cache-bust runtime-loaded assets such as the locale JSON files.
declare const __SOURCE_VERSION__: string;
