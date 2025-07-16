import { PostHogProvider as PostHogProviderBase } from "posthog-js/react";
import { PropsWithChildren } from "react";
import { useConfig } from './config';

/**
 * A global provider in charge of initializing PostHog if the config has
 * the POSTHOG_KEY and POSTHOG_HOST set.
 */
export const PostHogProvider = ({ children, }: PropsWithChildren) => {
  const config = useConfig();

  if (!config?.POSTHOG_KEY || !config?.POSTHOG_HOST) {
    return children;
  }

  return (
    <PostHogProviderBase
      apiKey={config?.POSTHOG_KEY}
      options={{
        api_host: config?.POSTHOG_HOST,
        defaults: "2025-05-24",
        debug: config.ENVIRONMENT === "development",
      }}
    >
      {children}
    </PostHogProviderBase>
  );
};
