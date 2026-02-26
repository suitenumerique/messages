import { AppLayout } from "@/features/layouts/components/main/layout";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { Breadcrumbs } from "@/features/ui/components/breadcrumbs";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { AdminMailDomainProvider, useAdminMailDomain } from "@/features/providers/admin-maildomain";
import useAbility, { Abilities } from "@/hooks/use-ability";
import ErrorPage from "next/error";
import { Toaster } from "@/features/ui/components/toaster";
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import { useTheme } from "@/features/providers/theme";
import { useState } from "react";
import { LayoutContext } from "../main";

type AdminLayoutProps = {
  children: React.ReactNode;
  currentTab?: string;
  actions?: React.ReactNode;
};

function AdminLayoutContent({
  children,
  currentTab,
  actions
}: AdminLayoutProps) {
  const { t } = useTranslation();
  const { selectedMailDomain } = useAdminMailDomain();
  const canViewDomainAdmin = useAbility(Abilities.CAN_VIEW_DOMAIN_ADMIN);

  // Build breadcrumb items
  const breadcrumbItems = [
    {
      content: (
        <Link href="/" className="c__breadcrumbs__button" title={t("Back to your inbox")}>
          <span className="c__breadcrumbs__avatar">
            <Icon name="mail" type={IconType.OUTLINED} size={IconSize.MEDIUM} />
          </span>
        </Link>
      )
    },
    {
      content: (
        <Link href="/domain" className="c__breadcrumbs__button">
          {t("Maildomains management")}
        </Link>
      )
    }
  ];

  if (selectedMailDomain) {
    breadcrumbItems.push({
      content: (
        <Link href={`/domain/${selectedMailDomain.id}`} className="c__breadcrumbs__button">
          {selectedMailDomain.name || selectedMailDomain.id}
        </Link>
      )
    });

    // Add current page to breadcrumbs if not on main addresses page
    if (currentTab && currentTab !== "addresses") {
      const tabLabels = {
        dns: t("DNS"),
        signatures: t("Signatures")
      };
      breadcrumbItems.push({
        content: (
          <span className="c__breadcrumbs__button active">
            {tabLabels[currentTab as keyof typeof tabLabels]}
          </span>
        )
      });
    }
  }

  // Build tabs if we're in a domain
  const tabs = selectedMailDomain ? [
    { id: "addresses", label: t("Addresses"), href: `/domain/${selectedMailDomain.id}`, icon: "inbox" },
    { id: "dns", label: t("DNS"), href: `/domain/${selectedMailDomain.id}/dns`, icon: "dns" },
    { id: "signatures", label: t("Signatures"), href: `/domain/${selectedMailDomain.id}/signatures`, icon: "drive_file_rename_outline" },
  ] : [];

  if (!canViewDomainAdmin) {
    return <ErrorPage statusCode={403} />;
  }

  return (
    <div id={SKIP_LINK_TARGET_ID} className="admin-page">
      <div className="admin-page__header">
        <div className="admin-page__breadcrumbs">
          <Breadcrumbs items={breadcrumbItems} />
        </div>

        {actions && (
          <div className="admin-page__actions">
            {actions}
          </div>
        )}
      </div>
      <section className="admin-page__body">
        {tabs.length > 0 && (
          <div className="admin-page__tabs">
            {tabs.map((tab) => (
              <Link
                key={tab.id}
                href={tab.href}
                className={`admin-page__tab ${currentTab === tab.id ? "admin-page__tab--active" : ""}`}
              >
                {tab.icon && <Icon name={tab.icon} type={IconType.OUTLINED} size={IconSize.MEDIUM} />}
                {tab.label}
              </Link>
            ))}
          </div>
        )}

        <div className="admin-page__content">
          {children}
        </div>
      </section>
    </div>
  );
}

export function AdminLayout(props: AdminLayoutProps) {
  const [leftPanelOpen, setLeftPanelOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const { theme, variant } = useTheme();

  return (
    <LayoutContext.Provider value={{
      toggleLeftPanel: () => setLeftPanelOpen(!leftPanelOpen),
      closeLeftPanel: () => setLeftPanelOpen(false),
      openLeftPanel: () => setLeftPanelOpen(true),
      isDragging,
      setIsDragging,
    }}>
      <AppLayout
        isLeftPanelOpen={false}
        setIsLeftPanelOpen={() => { }}
        leftPanelContent={null}
        hideSearch
        hideLeftPanelOnDesktop={true}
        icon={<Link href="/"><img src={`/images/${theme}/app-logo-${variant}.svg`} alt="logo" height={40} /></Link>}
      >
        <AdminMailDomainProvider>
          <AdminLayoutContent {...props} />
          <Toaster />
        </AdminMailDomainProvider>
      </AppLayout>
    </LayoutContext.Provider>
  );
}
