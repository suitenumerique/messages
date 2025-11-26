// Read more about this file in the documentation:
// https://nextjs.org/docs/app/api-reference/file-conventions/instrumentation-client


import * as Sentry from "@sentry/nextjs";

const isSentryEnabled = process.env.NEXT_PUBLIC_SENTRY_DSN && process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT;

if (isSentryEnabled) {
    Sentry.init({
        dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
        environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT,
    });
    Sentry.setTag("application", "frontend");
}
