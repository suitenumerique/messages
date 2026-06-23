import { ConfigRetrieve200, useConfigRetrieve } from "@/features/api/gen";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { PropsWithChildren, createContext, useContext, useMemo } from "react";

type AppConfig = Omit<ConfigRetrieve200, 'DRIVE'> & Required<Pick<ConfigRetrieve200, 'DRIVE'>>;

const DEFAULT_DRIVE_CONFIG: NonNullable<ConfigRetrieve200['DRIVE']> = {
    sdk_url: "",
    api_url: "",
    file_url: "",
    preview_url: "",
    app_name: "Drive",
}

const DEFAULT_CONFIG: AppConfig = {
    ENVIRONMENT: "",
    BUILD_VERSION: "dev",
    BUILD_DATE: "",
    LANGUAGES: [],
    LANGUAGE_CODE: "",
    AI_ENABLED: false,
    FEATURE_AI_SUMMARY: false,
    FEATURE_AI_AUTOLABELS: false,
    FEATURE_MAILBOX_ADMIN_CHANNELS: [],
    SCHEMA_CUSTOM_ATTRIBUTES_USER: {},
    SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN: {},
    MAX_OUTGOING_ATTACHMENT_SIZE: 0,
    MAX_OUTGOING_BODY_SIZE: 0,
    MAX_INCOMING_EMAIL_SIZE: 0,
    MAX_RECIPIENTS_PER_MESSAGE: 0,
    MAX_TEMPLATE_IMAGE_SIZE: 0,
    IMAGE_PROXY_ENABLED: false,
    FEATURE_MAILDOMAIN_CREATE: true,
    FEATURE_MAILDOMAIN_MANAGE_ACCESSES: true,
    FEATURE_MAILDOMAIN_MANAGE_TOTP: false,
    FEATURE_THREAD_SPLIT: true,
    DRIVE: DEFAULT_DRIVE_CONFIG,
    MESSAGES_MANUAL_RETRY_MAX_AGE: 0,
    FRONTEND_SILENT_LOGIN_ENABLED: false,
    REALTIME_ENABLED: false,
    REALTIME_POLL_INTERVAL_LIVE: 1800,
    REALTIME_POLL_INTERVAL_FALLBACK: 60,
}

const ConfigContext = createContext<AppConfig>(DEFAULT_CONFIG)

// Re-fetch /config periodically so a long-lived tab picks up runtime changes
// (e.g. REALTIME_ENABLED toggled to shed load) without a reload — the realtime
// provider reacts to `enabled` changing. Cheap (one anonymous request/hour) and
// the natural place to later detect a new build and prompt a reload, once
// /config carries a version.
const CONFIG_REFETCH_INTERVAL_MS = 60 * 60_000; // 1 hour

/**
 * A global provider in charge of fetching the config at first load
 * and sharing it to the app.
 */
export const ConfigProvider = ({ children }: PropsWithChildren) => {
    const { data: config, isFetched } = useConfigRetrieve({
      query: { refetchInterval: CONFIG_REFETCH_INTERVAL_MS },
    });
    const configValue = useMemo(() => {
      if (!config) return DEFAULT_CONFIG;
      return {
        ...config?.data,
        DRIVE: config?.data?.DRIVE ?? DEFAULT_DRIVE_CONFIG,
      }
    }, [config])

    if (!isFetched) {
        return (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100vh",
            }}
          >
            <Spinner size="xl"/>
          </div>
        );
      }

    return (
        <ConfigContext.Provider value={configValue}>
            {children}
        </ConfigContext.Provider>
    )
}

export const useConfig = () => {
    const config = useContext(ConfigContext)
    if (!config) {
        throw new Error("`useConfig` must be used within a children of `ConfigProvider`.")
    }
    return config
}
