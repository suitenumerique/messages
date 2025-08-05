import { ConfigRetrieve200, useConfigRetrieve } from "@/features/api/gen";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { PropsWithChildren, createContext, useContext, useMemo } from "react";

const DEFAULT_CONFIG: ConfigRetrieve200 = {
    ENVIRONMENT: "",
    POSTHOG_KEY: null,
    POSTHOG_HOST: null,
    POSTHOG_SURVEY_ID: null,
    LANGUAGES: [],
    LANGUAGE_CODE: "",
    AI_ENABLED: false,
    AI_FEATURE_SUMMARY_ENABLED: false,
    SCHEMA_CUSTOM_ATTRIBUTES_USER: {},
    SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN: {},
}

const ConfigContext = createContext<ConfigRetrieve200>(DEFAULT_CONFIG)

/**
 * A global provider in charge of fetching the config at first load
 * and sharing it to the app.
 */
export const ConfigProvider = ({ children }: PropsWithChildren) => {
    const { data: config, isFetched } = useConfigRetrieve();
    const configValue = useMemo(() => config?.data ?? DEFAULT_CONFIG, [config])

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
            <Spinner />
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
