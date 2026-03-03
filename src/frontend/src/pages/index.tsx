import { useTranslation } from "react-i18next";
import { Hero, HomeGutter, Footer, ProConnectButton } from "@gouvfr-lasuite/ui-kit";
import { login, useAuth } from "@/features/auth";
import { MainLayout } from "@/features/layouts/components/main";
import { LanguagePicker } from "@/features/layouts/components/main/language-picker";
import { AppLayout } from "@/features/layouts/components/main/layout";
import { LeftPanel } from "@/features/layouts/components/main/left-panel";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { FeedbackWidget } from "@/features/ui/components/feedback-widget";
import { useTheme } from "@/features/providers/theme";

export default function HomePage() {
  const { t } = useTranslation();
  const { theme, variant, themeConfig } = useTheme();
  const { user } = useAuth();

  if (user) {
    return <MainLayout />;
  }


  return (
    <AppLayout
        hideLeftPanelOnDesktop
        leftPanelContent={<LeftPanel />}
        rightHeaderContent={<LanguagePicker />}
        icon={<img src={`/images/${theme}/app-logo-${variant}.svg`} alt="logo" height={40} />}
      >
      <div id={SKIP_LINK_TARGET_ID} className="app__home">
        <HomeGutter>
          <Hero
            logo={<img src={`/images/${theme}/app-icon-${variant}.svg`} alt="Messages Logo" width={64} />}
            title={t("Simple and intuitive messaging")}
            banner="/images/banner.webp"
            subtitle={t("Send and receive your messages in an instant.")}
            mainButton={<ProConnectButton onClick={login} />}
          />
        </HomeGutter>
        {themeConfig.footer && (
          <Footer {...themeConfig.footer} />
        )}
      </div>
      <FeedbackWidget />
      </AppLayout>
  );
}
