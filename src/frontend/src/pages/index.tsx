import { GlobalLayout } from "@/features/layouts/components/global/GlobalLayout";
import Head from "next/head";
import { useTranslation } from "next-i18next";
import { DefaultLayout } from "@/features/layouts/components/default/DefaultLayout";
import { ProConnectButton } from "@gouvfr-lasuite/ui-kit";
import { login, logout, useAuth } from "@/features/auth/Auth";

export default function HomePage() {
  const { t } = useTranslation();
  const { user } = useAuth();

  if (user) {
    return <>
      <div>
        <h1>Logged in as {user.email}</h1>
        <button onClick={logout}>Logout</button>
      </div>
    </>
  }

  return (
    <>
      <Head>
        <title>{t("app_title")}</title>
        <meta name="description" content={t("app_description")} />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.png" />
      </Head>
      <div>
        {t("welcome")}
        <ProConnectButton onClick={login} />
      </div>
    </>
  );
}

HomePage.getLayout = function getLayout(page: React.ReactElement) {
  return (
    <GlobalLayout>
      <DefaultLayout>{page}</DefaultLayout>
    </GlobalLayout>
  );
};
