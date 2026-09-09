import { createFileRoute, useSearch } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Hero, HomeGutter, Footer, ProConnectButton, useResponsive } from "@gouvfr-lasuite/ui-kit";

import { login, useAuth } from "@/features/auth";
import { MainLayout } from "@/features/layouts/components/main";
import { LanguagePicker } from "@/features/layouts/components/main/language-picker";
import { AppLayout } from "@/features/layouts/components/main/layout";
import { LeftPanel } from "@/features/layouts/components/main/left-panel";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { FeedbackWidget } from "@/features/ui/components/feedback-widget";
import { useTheme } from "@/features/providers/theme";
import { useDocumentTitle } from "@/hooks/use-document-title";

const HomePage = () => {
  const { t } = useTranslation();
  const { theme, variant, themeConfig } = useTheme();
  const { user } = useAuth();
  const { isDesktop } = useResponsive();
  const searchParams = useSearch({ strict: false }) as { next?: string };
  const headerIconName = isDesktop ? `app-logo-${variant}` : `app-icon-${variant}`;
  const heroIconName = isDesktop ? `app-icon-${variant}` : `app-logo-${variant}`;

  useDocumentTitle();

  if (user) {
    return <MainLayout />;
  }

  const handleLogin = () => {
    const nextParam = searchParams.next;
    login(typeof nextParam === "string" ? nextParam : undefined);
  };

  return (
    <AppLayout
      hideLeftPanelOnDesktop
      leftPanelContent={<LeftPanel />}
      rightHeaderContent={<LanguagePicker />}
      icon={
      <img src={`/images/${theme}/${headerIconName}.svg`} alt={t("logo")} height={40} style={{ flexShrink: 0, minWidth: 0 }} />
     }
    >
      <div id={SKIP_LINK_TARGET_ID} className="app__home">
        <HomeGutter>
          <Hero
            logo={<img src={`/images/${theme}/${heroIconName}.svg`} alt={t("Messages Logo")} width={isDesktop ? 64 : 200} />}
            title={t("Simple and intuitive messaging")}
            banner={`/images/banner-${variant}.webp`}
            subtitle={t("Send and receive your messages in an instant.")}
            mainButton={<ProConnectButton onClick={handleLogin} />}
          />
        </HomeGutter>
        {themeConfig.footer && (
          <Footer {...themeConfig.footer} />
        )}
      </div>
      <FeedbackWidget />
    </AppLayout>
  );
};

export const Route = createFileRoute("/")({
  component: HomePage,
});
