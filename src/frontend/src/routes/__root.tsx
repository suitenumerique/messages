import { createRootRoute, Outlet } from "@tanstack/react-router";
import { useEffect } from "react";
import {
  MutationCache,
  Query,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { TanStackRouterDevtools } from "@tanstack/react-router-devtools";
import { useTranslation } from "react-i18next";

import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { errorToString } from "@/features/api/api-error";
import { Auth } from "@/features/auth";
import { ConfigProvider } from "@/features/providers/config";
import ErrorBoundary from "@/features/errors/error-boundary";
import ThemeProvider from "@/features/providers/theme";

const onError = (error: Error, query: unknown) => {
  if ((query as Query).meta?.noGlobalError) {
    return;
  }
  addToast(
    <ToasterItem type="error">
      <span>{errorToString(error)}</span>
    </ToasterItem>,
    {
      toastId: "APPLICATION_ERROR_TOAST",
    },
  );
};

const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => onError(error, mutation),
  }),
  queryCache: new QueryCache({
    onError: (error, query) => onError(error, query),
  }),
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

const DEFAULT_THEME = "white-label";
const parseTheme = (raw: string | undefined): string => {
  if (!raw) return DEFAULT_THEME;
  try {
    return JSON.parse(raw)?.theme ?? DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
};
const THEME = parseTheme(import.meta.env.NEXT_PUBLIC_THEME_CONFIG);

// Inject theme-aware SVG favicons into <head>. `index.html` only ships the
// fixed PWA bitmap icons, which are not theme-aware.
const installThemeFavicons = (theme: string) => {
  const variants: Array<{ media: string; href: string }> = [
    { media: "(prefers-color-scheme: light)", href: `/images/${theme}/favicon-light.svg` },
    { media: "(prefers-color-scheme: dark)", href: `/images/${theme}/favicon-dark.svg` },
  ];
  const links = variants.map(({ media, href }) => {
    const el = document.createElement("link");
    el.rel = "icon";
    el.type = "image/svg+xml";
    el.media = media;
    el.href = href;
    document.head.appendChild(el);
    return el;
  });
  return () => links.forEach((el) => el.remove());
};

const RootShell = () => {
  const { t } = useTranslation();

  useEffect(() => {
    document.title = t("Messaging");
  }, [t]);

  useEffect(() => installThemeFavicons(THEME), []);

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
