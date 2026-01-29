import { format, isToday, isSameWeek, isYesterday, isSameYear } from 'date-fns';
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
