import type { ReactElement, ReactNode } from "react";
import type { NextPage } from "next";
import type { AppProps } from "next/app";
import { CunninghamProvider } from "@gouvfr-lasuite/ui-kit";
import {
  MutationCache,
  Query,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import "../styles/main.scss";
import "../features/i18n/initI18n";
import {
  addToast,
  ToasterItem,
} from "@/features/ui/components/toaster/Toaster";
import { errorToString } from "@/features/api/APIError";
import Head from "next/head";
import { useTranslation } from "react-i18next";

export type NextPageWithLayout<P = {}, IP = P> = NextPage<P, IP> & {
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
    </ToasterItem>
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
    },
  },
});

export default function MyApp({ Component, pageProps }: AppPropsWithLayout) {
  // Use the layout defined at the page level, if available
  const { t } = useTranslation();
  const getLayout = Component.getLayout ?? ((page) => page);

  return (
    <>
      <Head>
      <title>{t("app_title")}</title>
        <link rel="icon" href="/images/favicon-light.svg" type="image/svg+xml" />
        <link
          rel="icon"
          href="/images/favicon-light.svg"
          type="image/svg+xml"
          media="(prefers-color-scheme: light)"
        />
        <link
          rel="icon"
          href="/images/favicon-dark.svg"
          type="image/svg+xml"
          media="(prefers-color-scheme: dark)"
        />
      </Head>
      <QueryClientProvider client={queryClient}>
        <CunninghamProvider>
          {getLayout(<Component {...pageProps} />)}
        </CunninghamProvider>
      </QueryClientProvider>
    </>
  );
}
