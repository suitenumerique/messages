import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import i18n from "@/features/i18n/initI18n";
import { useMailboxesStatsOverviewRetrieve } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import type { Timeframe } from "./index";

type StatisticsOverviewProps = {
  mailboxId: string;
  timeframe: Timeframe;
};

/** Headline counts (conversations / messages / sent) for the period. */
export const StatisticsOverview = ({
  mailboxId,
  timeframe,
}: StatisticsOverviewProps) => {
  const { t } = useTranslation();
  const language = i18n.resolvedLanguage;

  const { data, isLoading, error } = useMailboxesStatsOverviewRetrieve(
    mailboxId,
    { timeframe },
  );

  if (isLoading) {
    return (
      <Banner type="info" icon={<Spinner />}>
        {t("Loading statistics...")}
      </Banner>
    );
  }

  if (error || !data) {
    return <Banner type="error">{t("Error while loading statistics")}</Banner>;
  }

  const stats = data.data;

  const split = (assigned: number, unassigned: number) =>
    t("{{assigned}} assigned · {{unassigned}} unassigned", {
      assigned: assigned.toLocaleString(language),
      unassigned: unassigned.toLocaleString(language),
    });

  const cards = [
    { label: t("Conversations"), value: stats.conversations },
    { label: t("Messages"), value: stats.messages },
    { label: t("Sent"), value: stats.sent },
    {
      label: t("Unanswered"),
      value: stats.unreplied,
      sub: split(stats.unreplied_assigned, stats.unreplied_unassigned),
    },
    {
      label: t("Unread"),
      value: stats.unread,
      sub: split(stats.unread_assigned, stats.unread_unassigned),
    },
  ];

  return (
    <div className="mailbox-settings__stat-cards">
      {cards.map((card) => (
        <div key={card.label} className="mailbox-settings__stat-card">
          <span className="mailbox-settings__stat-card-value">
            {card.value.toLocaleString(language)}
          </span>
          <span className="mailbox-settings__stat-card-label">
            {card.label}
          </span>
          {card.sub && (
            <span className="mailbox-settings__stat-card-sub">{card.sub}</span>
          )}
        </div>
      ))}
    </div>
  );
};
