import clsx from "clsx";
import { useTranslation } from "react-i18next";
import { MailboxResponseTimes } from "@/features/api/gen";
import { formatDuration } from "./format-duration";

type ResponseTimesChartProps = {
  data: MailboxResponseTimes;
};

type ChartRow = {
  key: string;
  label: string;
  replied: number;
  averageSeconds: number | null;
  // Global: all unanswered emails in the period. Author: conversations
  // assigned to them still waiting for a reply.
  pending: number;
};

/**
 * Hand-rolled reply-speed chart (no charting library). One row per line — the
 * global average on top, then each author fastest first.
 *
 * The global average is the anchor: its bar always fills to the halfway mark.
 * Every other bar is scaled against it — `(avg / globalAvg) × 50%` — so the
 * average time sits at 50%, twice the average fills the track, and anything
 * slower is clamped to a full bar. Colour follows the same reading: the global
 * average stays blue, authors faster than average are green, up to 2× average
 * orange, and 2× or slower red. A tick marks the 50% (= average) line.
 */
export const ResponseTimesChart = ({ data }: ResponseTimesChartProps) => {
  const { t } = useTranslation();

  const globalRow: ChartRow = {
    key: "__global",
    label: t("Global"),
    replied: data.replied,
    averageSeconds: data.average_response_seconds,
    pending: data.unreplied,
  };

  // Authors sorted by average response time ascending (fastest first); those
  // with no replies (null average) sink to the bottom.
  const authorRows: ChartRow[] = data.authors
    .map((author, index) => ({
      key: `${index}-${author.author}`,
      label: author.author,
      replied: author.replied,
      averageSeconds: author.average_response_seconds,
      pending: author.unanswered,
    }))
    .sort(
      (a, b) =>
        (a.averageSeconds ?? Infinity) - (b.averageSeconds ?? Infinity),
    );

  const rows = [globalRow, ...authorRows];
  const globalAverage = data.average_response_seconds;

  // Bar length: the global average fills to 50%; everyone scales from there,
  // capped at a full bar (reached at 2× the average).
  const fillWidth = (averageSeconds: number | null) =>
    averageSeconds == null || !globalAverage
      ? 0
      : Math.min((averageSeconds / globalAverage) * 50, 100);

  const fillModifier = (row: ChartRow) => {
    if (row.key === "__global") {
      return "mailbox-settings__chart-gauge-fill--avg";
    }
    if (row.averageSeconds == null || !globalAverage) {
      return "mailbox-settings__chart-gauge-fill--none";
    }
    const ratio = row.averageSeconds / globalAverage;
    if (ratio <= 1) {
      return "mailbox-settings__chart-gauge-fill--fast";
    }
    if (ratio < 2) {
      return "mailbox-settings__chart-gauge-fill--ok";
    }
    return "mailbox-settings__chart-gauge-fill--slow";
  };

  return (
    <ul className="mailbox-settings__chart">
      {rows.map((row) => (
        <li
          key={row.key}
          className={clsx("mailbox-settings__chart-row", {
            "mailbox-settings__chart-row--global": row.key === "__global",
          })}
        >
          <span className="mailbox-settings__chart-label" title={row.label}>
            {row.label}
          </span>
          <svg
            className="mailbox-settings__chart-gauge"
            viewBox="0 0 100 4"
            preserveAspectRatio="none"
            role="img"
            aria-label={t("Average {{average}} over {{replied}} replies", {
              average: formatDuration(row.averageSeconds, t),
              replied: row.replied,
            })}
          >
            <rect
              x="0"
              y="0"
              height="4"
              width={fillWidth(row.averageSeconds)}
              rx="0.5"
              className={clsx(
                "mailbox-settings__chart-gauge-fill",
                fillModifier(row),
              )}
            />
            <rect
              x="49.75"
              y="0"
              width="0.5"
              height="4"
              className="mailbox-settings__chart-gauge-marker"
            />
          </svg>
          <span className="mailbox-settings__chart-avg">
            {formatDuration(row.averageSeconds, t)}
          </span>
          <span className="mailbox-settings__chart-count">
            {t("{{count}} replies", { count: row.replied })}
          </span>
          <span
            className={clsx("mailbox-settings__chart-pending", {
              "mailbox-settings__chart-pending--empty": row.pending === 0,
            })}
          >
            {t("{{count}} pending", { count: row.pending })}
          </span>
        </li>
      ))}
    </ul>
  );
};
