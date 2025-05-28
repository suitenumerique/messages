import { GlobalLayout } from "@/features/layouts/components/global/global-layout";
import Head from "next/head";
import { useTranslation } from "react-i18next";
import { Hero, HomeGutter, Footer, ProConnectButton } from "@gouvfr-lasuite/ui-kit";
import { login, useAuth } from "@/features/auth";
import { MainLayout } from "@/features/layouts/components/main";
import { LanguagePicker } from "@/features/layouts/components/main/language-picker";
import { AppLayout } from "@/features/layouts/components/main/layout";
import { LeftPanel } from "@/features/layouts/components/main/left-panel";

export default function HomePage() {

  const { t } = useTranslation();
  const { user } = useAuth();

  if (user) {
    return <MainLayout />;
  }


  return (
    <div className="app__home">
      <Head>
        <title>{t("app_title")}</title>
        <meta name="description" content={t("app_description")} />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.png" />
      </Head>
      <HomeGutter>
        <Hero
          logo={<img src="/images/app-icon.svg" alt="Messages Logo" width={64} />}
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
      <GlobalLayout>
        <AppLayout
          hideLeftPanelOnDesktop
          leftPanelContent={<LeftPanel />}
          rightHeaderContent={<LanguagePicker />}
          icon={<img src="/images/app-logo.svg" alt="logo" height={32} />}
        >
          {page}
        </AppLayout>
      </GlobalLayout>
  );
};
