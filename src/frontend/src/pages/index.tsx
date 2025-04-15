import { GlobalLayout } from "@/features/layouts/components/global/GlobalLayout";
import Head from "next/head";
import { useTranslation } from "next-i18next";
import { DefaultLayout } from "@/features/layouts/components/default/DefaultLayout";
import { Hero, HomeGutter, Footer, ProConnectButton } from "@gouvfr-lasuite/ui-kit";
import { login, useAuth } from "@/features/auth/Auth";

export default function HomePage() {
  const { t } = useTranslation();
  const { user } = useAuth();

  if (user) {
    return <>
      <div></div>
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
      <HomeGutter>
        <Hero
          logo={<img src="/images/app-icon.svg" alt="DocLogo" width={64} />}
          title={t("home.title")}
          banner="/images/banner.png"
          subtitle={t("home.subtitle")}
          mainButton={<ProConnectButton onClick={login} />}
        />
      </HomeGutter>
      <Footer />
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
