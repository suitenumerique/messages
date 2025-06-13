import { GlobalLayout } from "@/features/layouts/components/global/global-layout";
import { useTranslation } from "react-i18next";
import { Hero, HomeGutter, Footer, ProConnectButton } from "@gouvfr-lasuite/ui-kit";
import { login, useAuth } from "@/features/auth";
import { MainLayout } from "@/features/layouts/components/main";
import { LanguagePicker } from "@/features/layouts/components/main/language-picker";
import { AppLayout } from "@/features/layouts/components/main/layout";
import { LeftPanel } from "@/features/layouts/components/main/left-panel";
import {Button} from "@openfun/cunningham-react";
import { useCunninghamTheme } from '@/styles';

export default function HomePage() {

  const { t } = useTranslation();
  const { user } = useAuth();
    const { componentTokens } =
        useCunninghamTheme();
    const withProConnect = componentTokens['home-proconnect'];
    const withFooter = componentTokens['footer'];

  if (user) {
    return <MainLayout />;
  }


  return (
    <div className="app__home">
      <HomeGutter>
        <Hero
          logo={<img src="/images/app-icon.svg" alt="Messages Logo" width={64} />}
          title={t("home.title")}
          banner="/images/banner.png"
          subtitle={t("home.subtitle")}
          mainButton={withProConnect ? <ProConnectButton onClick={login} /> : <Button onClick={login}>{t("home.loginMessage")}</Button>}
        />
      </HomeGutter>
      {withFooter && <Footer />}
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
