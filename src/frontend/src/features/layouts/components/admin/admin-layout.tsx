import { AppLayout } from "@/features/layouts/components/main/layout";
import { SKIP_LINK_TARGET_ID } from "@/features/ui/components/skip-link";
import { Breadcrumbs } from "@/features/ui/components/breadcrumbs";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { AdminMailDomainProvider, useAdminMailDomain } from "@/features/providers/admin-maildomain";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { ErrorPage } from "@/features/ui/components/error-page";
import { Toaster } from "@/features/ui/components/toaster";
import { Badge, Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import { useTheme } from "@/features/providers/theme";
import { LayoutProvider } from "@/features/layouts/components/layout-context";

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
        <Link to="/" className="c__breadcrumbs__button" title={t("Back to your inbox")}>
          <span className="c__breadcrumbs__avatar">
            <Icon name="mail" type={IconType.OUTLINED} size={IconSize.MEDIUM} />
          </span>
        </Link>
      )
    },
    {
      content: (
        <Link to="/domain" className="c__breadcrumbs__button">
          {t("Maildomains management")}
        </Link>
      )
    }
  ];

  if (selectedMailDomain) {
    breadcrumbItems.push({
      content: (
        <Link to="/domain/$maildomainId" params={{ maildomainId: selectedMailDomain.id }} className="c__breadcrumbs__button">
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
      {
          id: "addresses",
          label: <div>{t("Addresses")} <Badge type="neutral">{selectedMailDomain.mailbox_count}</Badge></div>,
          to: "/domain/$maildomainId" as const,
          icon: "inbox"
      },
    { id: "dns", label: t("DNS"), to: "/domain/$maildomainId/dns" as const, icon: "dns" },
    { id: "signatures", label: t("Signatures"), to: "/domain/$maildomainId/signatures" as const, icon: "drive_file_rename_outline" },
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
                to={tab.to}
                params={{ maildomainId: selectedMailDomain!.id }}
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
  const { theme, variant } = useTheme();
  const { t } = useTranslation();

  return (
    <LayoutProvider>
      <AppLayout
        isLeftPanelOpen={false}
        setIsLeftPanelOpen={() => { }}
        leftPanelContent={null}
        hideSearch
        hideLeftPanelOnDesktop={true}
        icon={<Link to="/"><img src={`/images/${theme}/app-logo-${variant}.svg`} alt={t("logo")} height={40} /></Link>}
      >
        <AdminMailDomainProvider>
          <AdminLayoutContent {...props} />
          <Toaster />
        </AdminMailDomainProvider>
      </AppLayout>
    </LayoutProvider>
  );
}
