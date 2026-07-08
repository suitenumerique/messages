import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import i18n from "@/features/i18n/initI18n";
import {
  TreeLabel,
  useLabelsList,
  useMailboxesStatsResponseTimesByLabelRetrieve,
} from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { formatDuration } from "./format-duration";
import type { Timeframe } from "./index";

type StatisticsByLabelProps = {
  mailboxId: string;
  timeframe: Timeframe;
};

type Totals = { received: number; replied: number; seconds: number };
type LabelRow = { node: TreeLabel; depth: number; totals: Totals };

/**
 * Response times grouped by label, rendered as the full (compact) label tree.
 * Each incoming email counts once per label on its thread. A parent row rolls
 * up its whole subtree, so the average is computed from summed response time
 * over summed replies, and "no reply" is received − replied. Empty branches
 * (no activity anywhere below them) are pruned.
 */
export const StatisticsByLabel = ({
  mailboxId,
  timeframe,
}: StatisticsByLabelProps) => {
  const { t } = useTranslation();
  const language = i18n.resolvedLanguage;

  const labelsQuery = useLabelsList({ mailbox_id: mailboxId });
  const statsQuery = useMailboxesStatsResponseTimesByLabelRetrieve(mailboxId, {
    timeframe,
  });

  if (labelsQuery.isLoading || statsQuery.isLoading) {
    return (
      <Banner type="info" icon={<Spinner />}>
        {t("Loading statistics...")}
      </Banner>
    );
  }

  if (labelsQuery.error || statsQuery.error || !statsQuery.data) {
    return <Banner type="error">{t("Error while loading statistics")}</Banner>;
  }

  const tree = labelsQuery.data?.data ?? [];
  const rawByLabel = new Map(
    statsQuery.data.data.labels.map((entry) => [entry.label, entry]),
  );

  // Full subtree totals (own + all descendants) for every node.
  const totalsByLabel = new Map<string, Totals>();
  const computeTotals = (node: TreeLabel): Totals => {
    const own = rawByLabel.get(node.id);
    const totals: Totals = {
      received: own?.received ?? 0,
      replied: own?.replied ?? 0,
      seconds: own?.response_seconds_total ?? 0,
    };
    for (const child of node.children) {
      const childTotals = computeTotals(child);
      totals.received += childTotals.received;
      totals.replied += childTotals.replied;
      totals.seconds += childTotals.seconds;
    }
    totalsByLabel.set(node.id, totals);
    return totals;
  };
  tree.forEach(computeTotals);

  // Flatten to display rows, pruning branches with no activity.
  const flatten = (nodes: readonly TreeLabel[], depth = 0): LabelRow[] =>
    nodes.flatMap((node) => {
      const totals = totalsByLabel.get(node.id);
      if (!totals || totals.received === 0) {
        return [];
      }
      return [
        { node, depth, totals },
        ...flatten(node.children, depth + 1),
      ];
    });
  const rows = flatten(tree);

  if (rows.length === 0) {
    return (
      <p className="mailbox-settings__section-description">
        {t("No labelled conversations in this period.")}
      </p>
    );
  }

  return (
    <div className="mailbox-settings__label-stats">
      <div className="mailbox-settings__label-stats-head">
        <span className="mailbox-settings__label-stats-label">
          {t("Label")}
        </span>
        <span className="mailbox-settings__label-stats-num">
          {t("Avg. response")}
        </span>
        <span className="mailbox-settings__label-stats-num">
          {t("Received")}
        </span>
        <span className="mailbox-settings__label-stats-num">
          {t("No reply")}
        </span>
      </div>
      {rows.map(({ node, depth, totals }) => (
        <div key={node.id} className="mailbox-settings__label-stats-row">
          <span
            className="mailbox-settings__label-stats-label"
            style={{ paddingLeft: `${depth * 1.25}rem` }}
            title={node.name}
          >
            <span
              className="mailbox-settings__label-stats-dot"
              style={{ backgroundColor: node.color }}
            />
            {node.display_name}
          </span>
          <span className="mailbox-settings__label-stats-num">
            {formatDuration(
              totals.replied ? Math.round(totals.seconds / totals.replied) : null,
              t,
            )}
          </span>
          <span className="mailbox-settings__label-stats-num">
            {totals.received.toLocaleString(language)}
          </span>
          <span className="mailbox-settings__label-stats-num">
            {(totals.received - totals.replied).toLocaleString(language)}
          </span>
        </div>
      ))}
    </div>
  );
};
