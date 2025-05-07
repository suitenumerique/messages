import { GlobalLayout } from "@/features/layouts/components/global/global-layout";
import Head from "next/head";
import { useTranslation } from "next-i18next";
import { Hero, HomeGutter, Footer, ProConnectButton } from "@gouvfr-lasuite/ui-kit";
import { login, useAuth } from "@/features/auth";
import { MainLayout } from "@/features/layouts/components/main";
import { Header } from "@/features/layouts/components/header";

export default function HomePage() {

  const { t } = useTranslation();
  const { user } = useAuth();

  if (user) {
    return <MainLayout />;
  }


  return (
    <div className="app__home">
      <Header />
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
    </div>
  );
}

HomePage.getLayout = function getLayout(page: React.ReactElement) {
  return (
      <GlobalLayout>{page}</GlobalLayout>
  );
};
