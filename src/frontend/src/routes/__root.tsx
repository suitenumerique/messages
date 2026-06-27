import { createRootRoute, Outlet } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools";

import { queryClient } from "@/features/api/query-client";
import { Auth } from "@/features/auth";
import { ConfigProvider } from "@/features/providers/config";
import ErrorBoundary from "@/features/errors/error-boundary";
import ThemeProvider from "@/features/providers/theme";

// Each route owns its document title through `useDocumentTitle`: a title set
// here would run after the routes' effects (React runs child effects first)
// and always override them.
const RootShell = () => {
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
