import * as Sentry from "@sentry/nextjs";

const isSentryEnabled = process.env.NEXT_PUBLIC_SENTRY_DSN && process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT;

type CaptureExceptionContext = Parameters<typeof Sentry.captureException>[1];

/**
 * Generic error handler to be called whenever we need to do error reporting throughout the app.
 * Passes errors to Sentry if available, logs the error to the console otherwise.
 */
export const handle = (error: unknown, context?: CaptureExceptionContext) => {
    if (isSentryEnabled) {
      Sentry.captureException(error, context);
    } else {
      console.error(error, context);
    }
  };
