import { useConfigRetrieve } from "@/features/api/gen";
import { AppConfig, resolveConfig } from "@/features/config/resolve";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { PropsWithChildren, createContext, useContext, useMemo } from "react";

const ConfigContext = createContext<AppConfig | undefined>(undefined)

/**
 * A global provider in charge of sharing the app configuration.
 * The query cache is primed during bootstrap (see `bootstrap.tsx`);
 * `staleTime: Infinity` keeps that primed data fresh so no second fetch
 * happens on mount. When the bootstrap fetch failed, the cache is empty and
 * the query retries here, letting the React tree recover a live config.
 */
export const ConfigProvider = ({ children }: PropsWithChildren) => {
    const { data: config, isFetched } = useConfigRetrieve({
      query: { staleTime: Infinity },
    });
    const configValue = useMemo(() => resolveConfig(config?.data), [config])

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
