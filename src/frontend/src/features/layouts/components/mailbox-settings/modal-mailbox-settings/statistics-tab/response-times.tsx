import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useMailboxesStatsResponseTimesRetrieve } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { ResponseTimesChart } from "./response-times-chart";
import type { Timeframe } from "./index";

type StatisticsResponseTimesProps = {
  mailboxId: string;
  timeframe: Timeframe;
};

/**
 * Response-times view: just the per-line chart. The headline numbers
 * (incoming / replied / average) live on the chart's "Global" line, so there
 * are no separate summary cards here.
 */
export const StatisticsResponseTimes = ({
  mailboxId,
  timeframe,
}: StatisticsResponseTimesProps) => {
  const { t } = useTranslation();

  const { data, isLoading, error } = useMailboxesStatsResponseTimesRetrieve(
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

  if (stats.incoming === 0) {
    return (
      <p className="mailbox-settings__section-description">
        {t("No incoming emails in this period.")}
      </p>
    );
  }

  return <ResponseTimesChart data={stats} />;
};
