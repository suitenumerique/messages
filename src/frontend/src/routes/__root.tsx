import { createRootRoute, Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools";
import { useTranslation } from "react-i18next";

import { queryClient } from "@/features/api/query-client";
import { Auth } from "@/features/auth";
import { ConfigProvider } from "@/features/providers/config";
import ErrorBoundary from "@/features/errors/error-boundary";
import ThemeProvider from "@/features/providers/theme";

const RootShell = () => {
  const { t } = useTranslation();

  useEffect(() => {
    document.title = t("Messaging");
  }, [t]);

  return (
    <QueryClientProvider client={queryClient}>
      <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />
      <TanStackRouterDevtools position="bottom-right" />
      <ErrorBoundary>
        <ConfigProvider>
          <ThemeProvider>
            <Auth>
              <Outlet />
            </Auth>
          </ThemeProvider>
        </ConfigProvider>
      </ErrorBoundary>
    </QueryClientProvider>
  );
};

export const Route = createRootRoute({
  component: RootShell,
});
