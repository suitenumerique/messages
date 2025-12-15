/**
 * Helper class for quota-related operations.
 * Centralizes parsing, formatting, and label generation for quota periods.
 */

export type PeriodType = 'd' | 'm' | 'y';

class QuotaHelper {
  /**
   * Period options for select dropdowns
   */
  static readonly PERIOD_OPTIONS: { label: string; value: PeriodType }[] = [
    { label: 'Day', value: 'd' },
    { label: 'Month', value: 'm' },
    { label: 'Year', value: 'y' },
  ];

  /**
   * Period labels for display
   */
  static readonly PERIOD_LABELS: Record<PeriodType, string> = {
    d: 'day',
    m: 'month',
    y: 'year',
  };

  /**
   * Short period labels (with slash prefix) for compact display
   */
  static readonly PERIOD_SHORT_LABELS: Record<PeriodType, string> = {
    d: '/day',
    m: '/month',
    y: '/year',
  };

  /**
   * Full period labels for tooltips and detailed views
   */
  static readonly PERIOD_FULL_LABELS: Record<PeriodType, string> = {
    d: 'per day',
    m: 'per month',
    y: 'per year',
  };

  /**
   * Contextual period labels (e.g., "today", "this month")
   */
  static readonly PERIOD_CONTEXTUAL_LABELS: Record<PeriodType, string> = {
    d: 'today',
    m: 'this month',
    y: 'this year',
  };

  /**
   * Check if a string is a valid period type
   */
  static isValidPeriod(period: string): period is PeriodType {
    return ['d', 'm', 'y'].includes(period);
  }

  /**
   * Parse a max_recipients string like "500/d" into { limit: string, period: PeriodType }
   */
  static parseMaxRecipients(
    value: string | null | undefined
  ): { limit: string; period: PeriodType } {
    if (!value) return { limit: '', period: 'd' };
    const parts = value.split('/');
    if (parts.length !== 2) return { limit: '', period: 'd' };
    const period = parts[1] as PeriodType;
    return {
      limit: parts[0],
      period: QuotaHelper.isValidPeriod(period) ? period : 'd',
    };
  }

  /**
   * Parse the global MAX_RECIPIENTS setting to get the limit for a specific period
   */
  static parseGlobalMaxRecipients(
    value: string | undefined
  ): { limit: number; period: PeriodType } | null {
    if (!value) return null;
    const parts = value.split('/');
    if (parts.length !== 2) return null;
    const limit = parseInt(parts[0], 10);
    if (isNaN(limit)) return null;
    const period = parts[1] as PeriodType;
    return {
      limit,
      period: QuotaHelper.isValidPeriod(period) ? period : 'd',
    };
  }

  /**
   * Format a max_recipients value from limit and period
   */
  static formatMaxRecipients(limit: string | number, period: PeriodType): string {
    return `${limit}/${period}`;
  }

  /**
   * Get the display label for a period
   */
  static getPeriodLabel(period: string, fallback?: string): string {
    if (QuotaHelper.isValidPeriod(period)) {
      return QuotaHelper.PERIOD_LABELS[period];
    }
    return fallback || period;
  }

  /**
   * Get the short label for a period (e.g., "/day")
   */
  static getPeriodShortLabel(period: string, fallback?: string): string {
    if (QuotaHelper.isValidPeriod(period)) {
      return QuotaHelper.PERIOD_SHORT_LABELS[period];
    }
    return fallback ? `/${fallback}` : `/${period}`;
  }

  /**
   * Get the full label for a period (e.g., "per day")
   */
  static getPeriodFullLabel(period: string, fallback?: string): string {
    if (QuotaHelper.isValidPeriod(period)) {
      return QuotaHelper.PERIOD_FULL_LABELS[period];
    }
    return fallback || period;
  }

  /**
   * Get the contextual label for a period (e.g., "today", "this month")
   */
  static getPeriodContextualLabel(period: string, fallback?: string): string {
    if (QuotaHelper.isValidPeriod(period)) {
      return QuotaHelper.PERIOD_CONTEXTUAL_LABELS[period];
    }
    return fallback || period;
  }
}

export default QuotaHelper;
