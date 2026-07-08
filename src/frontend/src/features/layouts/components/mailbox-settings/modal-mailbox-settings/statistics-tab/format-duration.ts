import { TFunction } from "i18next";

/**
 * Human-readable duration from a number of seconds (or null → em dash).
 * Uses the largest two units (e.g. "2 h 15 min", "3 j 4 h", "45 min", "30 s").
 * "h"/"min"/"s" are locale-neutral; only the day unit is translated.
 */
export const formatDuration = (
  seconds: number | null,
  t: TFunction,
): string => {
  if (seconds == null) {
    return "—";
  }
  const total = Math.round(seconds);
  if (total < 60) {
    return `${total} s`;
  }
  if (total < 3600) {
    return `${Math.round(total / 60)} min`;
  }
  if (total < 86400) {
    const hours = Math.floor(total / 3600);
    const minutes = Math.round((total % 3600) / 60);
    return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
  }
  const days = Math.floor(total / 86400);
  const hours = Math.round((total % 86400) / 3600);
  const daysLabel = t("{{days}} d", { days });
  return hours ? `${daysLabel} ${hours} h` : daysLabel;
};
