import { format, isToday, isSameWeek, isYesterday, isSameYear, isValid } from 'date-fns';
// @WARN: This import is surely importing to much locales, later we should
// import only the needed locales
import * as locales from 'date-fns/locale';
import i18n from '../i18n/initI18n';

/** A trailing UTC offset, with optional seconds (see ``hasOffset``). */
const OFFSET_RE = /(?:Z|([+-])(\d{2}):?(\d{2})(?::?(\d{2})(?:\.\d+)?)?)$/i;

export class DateHelper {
  /**
   * Formats a date string based on how recent it is:
   * - Today: displays time (HH:mm)
   * - Less than 1 month: displays short date (e.g., "3 mars")
   * - Otherwise: displays full date (DD/MM/YYYY)
   *
   * @param dateString - The date string to format
   * @param locale - The locale code (e.g., 'fr', 'en')
   * @param showTime - Whether to show the time (default: true)
   * @returns Formatted date string
   */
  public static formatDate(dateString: string, lng: string = 'en', showTime: boolean = true): string {
    const date = new Date(dateString);
    // date-fns throws on an invalid date, during render.
    if (!isValid(date)) return '';
    const locale = lng.length > 2 ? lng.split('-')[0] : lng;
    const dateLocale = locales[locale as keyof typeof locales];

    if (isToday(date)) {
      if (showTime) {
        return format(date, 'HH:mm', { locale: dateLocale });
      }
      return i18n.t('Today');
    }

    if (isYesterday(date)) {
      return i18n.t('Yesterday');
    }

    if (isSameWeek(date, Date.now())) {
      return format(date, 'EEEE', { locale: dateLocale });
    }

    if (isSameYear(date, Date.now())) {
      return format(date, 'd MMM', { locale: dateLocale });
    }

    return format(date, 'dd/MM/yyyy', { locale: dateLocale });
  }

  /**
   * Format a wall-clock time (`YYYY-MM-DDTHH:mm[:ss]`) as written, with its
   * time, in a locale-aware form (e.g. "Oct 20, 2026, 6:00 PM" or
   * "20 oct. 2026, 18:00"). In no time zone: read as a local time, one in a
   * skipped hour of this browser's zone would show an hour later.
   *
   * @param wallClock - The wall-clock time to format
   * @param lng - The locale code (e.g., 'fr', 'en-US')
   * @returns Formatted date and time, or undefined if the time is invalid
   */
  public static formatWallClock(wallClock: string, lng: string = 'en'): string | undefined {
    const instant = DateHelper.fromWallClock(wallClock, 'UTC');
    if (Number.isNaN(instant)) return undefined;
    return new Intl.DateTimeFormat(lng, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(instant);
  }

  /**
   * The IANA time zone `value` names, or UTC when it is not a non-empty
   * string or this browser does not know it. Mirrors how the backend reads
   * an auto-reply's time zone.
   */
  public static resolveTimeZone(value: unknown): string {
    if (typeof value !== 'string' || !value) return 'UTC';
    try {
      new Intl.DateTimeFormat('en-US', { timeZone: value });
      return value;
    } catch {
      return 'UTC';
    }
  }

  /**
   * Whether two IANA names are the same zone for this browser, aliases
   * included ("Asia/Kolkata" and "Asia/Calcutta", "UTC" and "Etc/UTC").
   */
  public static isSameTimeZone(a: string, b: string): boolean {
    const canonical = (timeZone: string) => {
      try {
        return new Intl.DateTimeFormat('en-US', { timeZone }).resolvedOptions().timeZone;
      } catch {
        return timeZone;
      }
    };
    return canonical(a) === canonical(b);
  }

  /**
   * The instant (ms since the epoch) of a wall-clock time
   * (`YYYY-MM-DDTHH:mm[:ss]`) in `timeZone`, read as the backend reads a naive
   * autoreply bound (Python zoneinfo, `fold=0`): in a repeated hour, the
   * first occurrence; in a skipped hour, with the offset from before the
   * change. NaN if the time or the zone is invalid.
   */
  public static fromWallClock(wallClock: string, timeZone: string): number {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(wallClock);
    if (!match) return NaN;
    const [year, month, day, hour, minute, second] = match.slice(1).map((part) => Number(part ?? 0));
    // Not ``Date.UTC``, which reads years 0-99 as 1900-1999.
    const asUtcDate = new Date(0);
    asUtcDate.setUTCFullYear(year, month - 1, day);
    asUtcDate.setUTCHours(hour, minute, second, 0);
    const asUtc = asUtcDate.getTime();
    if (Number.isNaN(asUtc)) return NaN;
    const offsetAt = (instant: number): number => {
      const wall = DateHelper.wallClockAt(instant, timeZone);
      if (!wall) return NaN;
      return Date.parse(`${wall}Z`) - instant;
    };
    // At most one change within a day: the offsets either side of it.
    const before = offsetAt(asUtc - 86400000);
    const after = offsetAt(asUtc + 86400000);
    if (Number.isNaN(before) || Number.isNaN(after)) return NaN;
    const candidates = [asUtc - before, asUtc - after].filter(
      (instant) => instant + offsetAt(instant) === asUtc,
    );
    // Two: a repeated hour, the first occurrence. None: a skipped hour, the
    // offset from before the change.
    return candidates.length ? Math.min(...candidates) : asUtc - before;
  }

  /**
   * Express an ISO date-time as a wall-clock time (`YYYY-MM-DDTHH:mm:ss`) in
   * the given time zone. A date-time without offset already is a wall-clock
   * time and is returned unchanged; one with an offset is an instant, which
   * is converted.
   *
   * @param dateString - The ISO date-time to express
   * @param timeZone - The IANA time zone (e.g., 'Europe/Paris')
   * @returns The wall-clock time, or undefined if the date or the time zone
   * is invalid
   */
  public static toWallClock(dateString: string, timeZone: string): string | undefined {
    if (!DateHelper.hasOffset(dateString)) return dateString;
    return DateHelper.wallClockAt(DateHelper.offsetInstant(dateString), timeZone);
  }

  /**
   * Whether an ISO date-time ends with a UTC offset: `Z`, `±HH:MM`, `±HHMM`,
   * or with seconds as Python writes them (`+01:00:30`).
   */
  public static hasOffset(dateString: string): boolean {
    return OFFSET_RE.test(dateString);
  }

  /**
   * The instant (ms since the epoch) of an ISO date-time with an offset,
   * including an offset with seconds, which `Date.parse` refuses. NaN
   * otherwise.
   */
  public static offsetInstant(dateString: string): number {
    const match = OFFSET_RE.exec(dateString);
    if (!match) return NaN;
    const local = Date.parse(`${dateString.slice(0, match.index)}Z`);
    if (!match[1]) return local;
    const sign = match[1] === '-' ? -1 : 1;
    const seconds = (Number(match[2]) * 60 + Number(match[3])) * 60 + Number(match[4] ?? 0);
    return local - sign * seconds * 1000;
  }

  /**
   * Compare two ISO date-times as instants, down to the microseconds the
   * backend stores and a Date drops: negative, zero or positive, NaN if
   * either cannot be read.
   */
  public static compareInstants(a: string, b: string): number {
    const diff = new Date(a).getTime() - new Date(b).getTime();
    if (diff !== 0 || Number.isNaN(diff)) return diff;
    return DateHelper.subMillisecond(a) - DateHelper.subMillisecond(b);
  }

  /**
   * The fraction of `value` below the millisecond (the digits a Date drops),
   * as a number comparable between values: "…00.123456Z" gives 0.456.
   */
  private static subMillisecond(value: string): number {
    const fraction = /T[\d:]+\.(\d+)/.exec(value)?.[1] ?? '';
    return fraction.length > 3 ? Number(`0.${fraction.slice(3)}`) : 0;
  }

  /**
   * The wall-clock time (`YYYY-MM-DDTHH:mm:ss`) of `instant` in `timeZone`.
   * Undefined if invalid, before year 1 (which Python cannot represent), or
   * if this browser does not know the zone.
   */
  private static wallClockAt(instant: number, timeZone: string): string | undefined {
    const date = new Date(instant);
    if (!isValid(date)) return undefined;
    try {
      const parts = Object.fromEntries(
        new Intl.DateTimeFormat('en-US', {
          timeZone,
          hourCycle: 'h23',
          era: 'short',
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }).formatToParts(date).map(({ type, value }) => [type, value]),
      );
      if (parts.era !== 'AD') return undefined;
      // ``numeric`` leaves years before 1000 unpadded.
      const year = parts.year.padStart(4, '0');
      return `${year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
    } catch {
      // Unknown time zone for this browser's ICU data.
      return undefined;
    }
  }

  /**
   * Bucket a date into one of three coarse ranges used by the thread-view
   * timeline to label collapsed runs of system events.
   */
  public static bucketDate(dateString: string | Date): 'today' | 'this_week' | 'older' {
    const date = typeof dateString === 'string' ? new Date(dateString) : dateString;
    if (isToday(date)) return 'today';
    if (isSameWeek(date, Date.now())) return 'this_week';
    return 'older';
  }

  /**
   * Format a timestamp for inline event display: always includes the time,
   * prefixed with the day/date for events outside of today so a user can tell
   * whether an assignment happened today at 14:32 or last Tuesday at 14:32
   * without hovering.
   */
  public static formatEventTimestamp(dateString: string, lng: string = 'en'): string {
    const date = new Date(dateString);
    if (!isValid(date)) return '';
    const locale = lng.length > 2 ? lng.split('-')[0] : lng;
    const dateLocale = locales[locale as keyof typeof locales];
    const time = format(date, 'HH:mm', { locale: dateLocale });

    if (isToday(date)) return time;
    if (isYesterday(date)) return `${i18n.t('Yesterday')} ${time}`;
    if (isSameWeek(date, Date.now())) return `${format(date, 'EEEE', { locale: dateLocale })} ${time}`;
    if (isSameYear(date, Date.now())) return `${format(date, 'd MMM', { locale: dateLocale })} ${time}`;
    return `${format(date, 'dd/MM/yyyy', { locale: dateLocale })} ${time}`;
  }

  /**
   * Compute a relative time between a given date and a time reference and
   * return a translation key and a count if needed.
   *
   * For now only past relative time is supported.
   *
   * @param dateString - The date string to format
   * @param timeRef - The time reference to compute the relative time from
   * @returns [translationKey, count]
   */
  public static formatRelativeTime(dateString: string | null | undefined, timeRef: Date | string = new Date()): string {
    if (!dateString) return "";
    const now = timeRef instanceof Date ? timeRef : new Date(timeRef);
    const date = new Date(dateString);
    const diffInSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (isNaN(diffInSeconds)) return "";
    if (diffInSeconds < 5) return i18n.t("just now");
    if (diffInSeconds < 60) return i18n.t("less than a minute ago");
    if (diffInSeconds < 3_600) {
      return i18n.t("{{count}} minutes ago", {
        count: Math.floor(diffInSeconds / 60),
        defaultValue_one: "{{count}} minute ago",
        defaultValue_other: "{{count}} minutes ago",
      })
    }
    if (diffInSeconds < 86_400) {
      return i18n.t("{{count}} hours ago", {
          count: Math.floor(diffInSeconds / 3600),
          defaultValue_one: "{{count}} hour ago",
          defaultValue_other: "{{count}} hours ago",
        });
    }
    if (diffInSeconds < 604_800) {
      return i18n.t("{{count}} days ago", {
        count: Math.floor(diffInSeconds / 86400),
        defaultValue_one: "{{count}} day ago",
        defaultValue_other: "{{count}} days ago",
      });
    }
    if (diffInSeconds < 2_592_000) {
      return i18n.t("{{count}} weeks ago", {
        count: Math.floor(diffInSeconds / 604800),
        defaultValue_one: "{{count}} week ago",
        defaultValue_other: "{{count}} weeks ago",
      });
    }
    if (diffInSeconds < 31_536_000) {
      return i18n.t("{{count}} months ago", {
        count: Math.floor(diffInSeconds / 2592000),
        defaultValue_one: "{{count}} month ago",
        defaultValue_other: "{{count}} months ago",
      });
    }
    return i18n.t("{{count}} years ago", {
      count: Math.floor(diffInSeconds / 31536000),
      defaultValue_one: "{{count}} year ago",
      defaultValue_other: "{{count}} years ago",
    });
  }
}
