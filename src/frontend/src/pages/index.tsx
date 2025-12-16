import { useTranslation } from "react-i18next";
import { Hero, HomeGutter, Footer, ProConnectButton, FooterProps } from "@gouvfr-lasuite/ui-kit";
import { login, useAuth } from "@/features/auth";
import { MainLayout } from "@/features/layouts/components/main";
import { LanguagePicker } from "@/features/layouts/components/main/language-picker";
import { AppLayout } from "@/features/layouts/components/main/layout";
import { LeftPanel } from "@/features/layouts/components/main/left-panel";
import { FeedbackWidget } from "@/features/ui/components/feedback-widget";

type ThemeConfig = {
  theme?: string;
  terms_of_service_url?: string;
  footer?: FooterProps;
};
let THEME_CONFIG: ThemeConfig = {};

try {
  THEME_CONFIG = JSON.parse(process.env.NEXT_PUBLIC_THEME_CONFIG || '{}');
} catch (error) {
  console.error('Error parsing theme config', error);
}

export default function HomePage() {

  const { t } = useTranslation();
  const { user } = useAuth();

  if (user) {
    return <MainLayout />;
  }


  return (
    <AppLayout
        hideLeftPanelOnDesktop
        leftPanelContent={<LeftPanel />}
        rightHeaderContent={<LanguagePicker />}
        icon={<img src="/images/app-logo.svg" alt="logo" height={32} />}
      >
      <div className="app__home">
        <HomeGutter>
          <Hero
            logo={<img src="/images/app-icon.svg" alt="Messages Logo" width={64} />}
            title={t("Simple and intuitive messaging")}
            banner="/images/banner.webp"
            subtitle={t("Send and receive your messages in an instant.")}
            mainButton={<ProConnectButton onClick={login} />}
          />
        </HomeGutter>
        {THEME_CONFIG.footer && (
          <Footer {...THEME_CONFIG.footer} />
        )}
      </div>
      <FeedbackWidget />
      </AppLayout>
  );
}
