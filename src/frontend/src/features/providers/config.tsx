import { ConfigRetrieve200, useConfigRetrieve } from "@/features/api/gen";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { PropsWithChildren, createContext, useContext, useMemo } from "react";

type AppConfig = Omit<ConfigRetrieve200, 'DRIVE'> & Required<Pick<ConfigRetrieve200, 'DRIVE'>>;

const DEFAULT_DRIVE_CONFIG: NonNullable<ConfigRetrieve200['DRIVE']> = {
    sdk_url: "",
    api_url: "",
    file_url: "",
    app_name: "Drive",
}

const DEFAULT_CONFIG: AppConfig = {
    ENVIRONMENT: "",
    LANGUAGES: [],
    LANGUAGE_CODE: "",
    AI_ENABLED: false,
    FEATURE_AI_SUMMARY: false,
    FEATURE_AI_AUTOLABELS: false,
    SCHEMA_CUSTOM_ATTRIBUTES_USER: {},
    SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN: {},
    MAX_OUTGOING_ATTACHMENT_SIZE: 0,
    MAX_OUTGOING_BODY_SIZE: 0,
    MAX_INCOMING_EMAIL_SIZE: 0,
    MAX_RECIPIENTS_PER_MESSAGE: 0,
    DRIVE: DEFAULT_DRIVE_CONFIG
}

const ConfigContext = createContext<AppConfig>(DEFAULT_CONFIG)

/**
 * A global provider in charge of fetching the config at first load
 * and sharing it to the app.
 */
export const ConfigProvider = ({ children }: PropsWithChildren) => {
    const { data: config, isFetched } = useConfigRetrieve();
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
