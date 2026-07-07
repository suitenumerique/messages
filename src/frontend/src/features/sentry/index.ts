import * as Sentry from "@sentry/react";

import { AppConfig } from "@/features/config/resolve";

/**
 * Initialize Sentry from the resolved application configuration.
 * Called during bootstrap, before the React tree renders, so that render
 * and routing errors are captured. No-op when Sentry is not configured.
 */
export const initSentry = (config: AppConfig) => {
  if (!config.SENTRY_DSN || !config.SENTRY_ENVIRONMENT) return;

  Sentry.init({
    dsn: config.SENTRY_DSN,
    environment: config.SENTRY_ENVIRONMENT,
    release: config.RELEASE,
  });
  Sentry.setTag("application", "frontend");
};
