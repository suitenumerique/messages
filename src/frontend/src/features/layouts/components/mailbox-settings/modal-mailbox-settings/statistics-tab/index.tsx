import { Select } from "@gouvfr-lasuite/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Mailbox } from "@/features/api/gen";
import { StatisticsResponseTimes } from "./response-times";
import { StatisticsByLabel } from "./by-label";

export type Timeframe =
  | "this_month"
  | "last_month"
  | "last_30_days"
  | "last_90_days";
type StatType = "response_times" | "by_label";

type MailboxSettingsStatisticsTabProps = {
  mailbox: Mailbox;
};

/**
 * Statistics tab: a statistics-type dropdown and a period dropdown drive the
 * view below. Only the response-times view exists right now — the "Overall"
 * counts view is temporarily removed (see `overview.tsx`, still wired to the
 * backend `stats/overview` endpoint for when it comes back), but the type
 * dropdown is kept so more views can slot back in. Reachable only by mailbox
 * admins (gated on `manage_accesses`).
 */
export const MailboxSettingsStatisticsTab = ({
  mailbox,
}: MailboxSettingsStatisticsTabProps) => {
  const { t } = useTranslation();
  const [statType, setStatType] = useState<StatType>("response_times");
  const [timeframe, setTimeframe] = useState<Timeframe>("this_month");

  return (
    <div className="mailbox-settings__tab mailbox-settings__statistics">
      <div className="mailbox-settings__statistics-controls">
        <Select
          label={t("Statistics")}
          hideLabel
          variant="classic"
          clearable={false}
          fullWidth
          value={statType}
          onChange={(e) => setStatType(String(e.target.value) as StatType)}
          options={[
            { label: t("Average response time"), value: "response_times" },
            { label: t("By label"), value: "by_label" },
          ]}
        />
        <Select
          label={t("Period")}
          hideLabel
          variant="classic"
          clearable={false}
          fullWidth
          value={timeframe}
          onChange={(e) => setTimeframe(String(e.target.value) as Timeframe)}
          options={[
            { label: t("This month"), value: "this_month" },
            { label: t("Last month"), value: "last_month" },
            { label: t("Last 30 days"), value: "last_30_days" },
            { label: t("Last 90 days"), value: "last_90_days" },
          ]}
        />
      </div>

      {statType === "by_label" ? (
        <StatisticsByLabel mailboxId={mailbox.id} timeframe={timeframe} />
      ) : (
        <StatisticsResponseTimes mailboxId={mailbox.id} timeframe={timeframe} />
      )}
    </div>
  );
};
