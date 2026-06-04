import * as Sentry from "@sentry/react";

const isSentryEnabled = import.meta.env.NEXT_PUBLIC_SENTRY_DSN && import.meta.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT;

if (isSentryEnabled) {
    Sentry.init({
        dsn: import.meta.env.NEXT_PUBLIC_SENTRY_DSN,
        environment: import.meta.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT,
    });
    Sentry.setTag("application", "frontend");
}
