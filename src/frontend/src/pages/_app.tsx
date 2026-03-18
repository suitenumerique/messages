import "@blocknote/mantine/style.css";
import "../styles/main.scss";
import { type ReactElement, type ReactNode } from "react";
import type { NextPage } from "next";
import type { AppProps } from "next/app";
import {
  MutationCache,
  Query,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import "../features/i18n/initI18n";
import {
  addToast,
  ToasterItem,
} from "@/features/ui/components/toaster";
import { errorToString } from "@/features/api/api-error";
import Head from "next/head";
import { useTranslation } from "react-i18next";
import { Auth } from "@/features/auth";
import { ConfigProvider } from "@/features/providers/config";
import ErrorBoundary from "@/features/errors/error-boundary";
import ThemeProvider from "@/features/providers/theme";

export type NextPageWithLayout<P = object, IP = P> = NextPage<P, IP> & {
  getLayout?: (page: ReactElement) => ReactNode;
};

type AppPropsWithLayout = AppProps & {
  Component: NextPageWithLayout;
};
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
    }
  );
};

const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (error, variables, context, mutation) => onError(error, mutation),
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

const THEME = JSON.parse(process.env.NEXT_PUBLIC_THEME_CONFIG || '{ "theme": "white-label" }').theme;

export default function MyApp({ Component, pageProps }: AppPropsWithLayout) {
  // Use the layout defined at the page level, if available
  const { t } = useTranslation();
  const getLayout = Component.getLayout ?? ((page) => page);

  return (
    <>
      <Head>
        <title>{t("Messaging")}</title>
        <meta name="description" content={t("Messaging")} />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link
          rel="icon"
          href={`/images/${THEME}/favicon-light.svg`}
          type="image/svg+xml"
          media="(prefers-color-scheme: light)"
        />
        <link
          rel="icon"
          href={`/images/${THEME}/favicon-dark.svg`}
          type="image/svg+xml"
          media="(prefers-color-scheme: dark)"
        />
      </Head>
      <QueryClientProvider client={queryClient}>
        <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />
        <ErrorBoundary>
          <ConfigProvider>
            <ThemeProvider>
              <Auth>
                {getLayout(<Component {...pageProps} />)}
              </Auth>
            </ThemeProvider>
          </ConfigProvider>
        </ErrorBoundary>
      </QueryClientProvider>
    </>
  );
}
