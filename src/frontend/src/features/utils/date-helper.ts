import { format, isToday, isSameWeek, isYesterday, isSameYear, isValid } from 'date-fns';
// @WARN: This import is surely importing to much locales, later we should
// import only the needed locales
import * as locales from 'date-fns/locale';
import i18n from '../i18n/initI18n';

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
   * Format a date with its time in a locale-aware long form
   * (e.g. "Oct 20, 2026, 6:00 PM" or "20 oct. 2026, 18:00").
   *
   * @param dateString - The date string to format
   * @param lng - The locale code (e.g., 'fr', 'en')
   * @returns Formatted date and time, or undefined if the date is invalid
   */
  public static formatDateTime(dateString: string, lng: string = 'en'): string | undefined {
    const date = new Date(dateString);
    if (!isValid(date)) return undefined;
    const locale = lng.length > 2 ? lng.split('-')[0] : lng;
    const dateLocale = locales[locale as keyof typeof locales];
    return format(date, 'PPp', { locale: dateLocale });
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
    if (!/(?:Z|[+-]\d{2}:?\d{2})$/i.test(dateString)) return dateString;
    const date = new Date(dateString);
    if (!isValid(date)) return undefined;
    try {
      const parts = Object.fromEntries(
        new Intl.DateTimeFormat('en-US', {
          timeZone,
          hourCycle: 'h23',
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        }).formatToParts(date).map(({ type, value }) => [type, value]),
      );
      return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
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
