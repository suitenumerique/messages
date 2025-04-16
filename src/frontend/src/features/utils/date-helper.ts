import { format, isToday, differenceInDays } from 'date-fns';
// @WARN: This import is surely importing to much locales, later we should
// import only the needed locales
import * as locales from 'date-fns/locale';

export class DateHelper {
  /**
   * Formats a date string based on how recent it is:
   * - Today: displays time (HH:mm)
   * - Less than 1 month: displays short date (e.g., "3 mars")
   * - Otherwise: displays full date (DD/MM/YYYY)
   * 
   * @param dateString - The date string to format
   * @param locale - The locale code (e.g., 'fr', 'en')
   * @returns Formatted date string
   */
  public static formatDate(dateString: string, locale: keyof typeof locales): string {
    const date = new Date(dateString);
    const daysDifference = differenceInDays(new Date(), date);
    const dateLocale = locales[locale as keyof typeof locales];

    if (isToday(date)) {
      return format(date, 'HH:mm', { locale: dateLocale });
    }

    if (daysDifference < 30) {
      return format(date, 'd MMMM', { locale: dateLocale });
    }

    return format(date, 'dd/MM/yyyy', { locale: dateLocale });
  }
}
