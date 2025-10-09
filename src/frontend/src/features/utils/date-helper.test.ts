import { DateHelper } from './date-helper';
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';

describe('DateHelper', () => {
  // Mock current date to 2024-03-15
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-04-17T16:00:00'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('formatDate', () => {
    it('should format time when date is today', () => {
      const todayDate = '2025-04-17T17:30:00';
      expect(DateHelper.formatDate(todayDate, 'fr')).toBe('17:30');
      expect(DateHelper.formatDate(todayDate, 'en')).toBe('17:30');
    });

    it('should format as short date when less than 30 days ago', () => {
      const recentDate = '2025-03-20T15:30:00';
      expect(DateHelper.formatDate(recentDate, 'fr')).toBe('20 mars');
      expect(DateHelper.formatDate(recentDate, 'en')).toBe('20 March');
    });

    it('should format as full date when more than 30 days ago', () => {
      const oldDate = '2024-01-15T15:30:00';
      expect(DateHelper.formatDate(oldDate, 'fr')).toBe('15/01/2024');
      expect(DateHelper.formatDate(oldDate, 'en')).toBe('15/01/2024');
    });

    it('should handle different locales correctly', () => {
      const date = '2025-03-20T15:30:00';
      expect(DateHelper.formatDate(date, 'fr')).toBe('20 mars');
      expect(DateHelper.formatDate(date, 'en')).toBe('20 March');
    });
  });

  describe('formatRelativeTime', () => {
    beforeEach(() => {
      // Mock Date.now() to return a fixed timestamp
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2024-01-15T12:00:00.000Z'));
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should return "just now" for times less than 5 seconds ago', () => {
      const dateString = '2024-01-15T11:59:57.000Z'; // 3 seconds ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('just now');
    });

    it('should return "less than minute ago" for times between 5 and 60 seconds ago', () => {
      const dateString = '2024-01-15T11:59:30.000Z'; // 30 seconds ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('less than a minute ago');
    });

    it('should return "minutes ago" with count for times between 1 minute and 1 hour ago', () => {
      const dateString = '2024-01-15T11:30:00.000Z'; // 30 minutes ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('30 minutes ago');
    });

    it('should return "hours ago" with count for times between 1 hour and 24 hours ago', () => {
      const dateString = '2024-01-15T09:00:00.000Z'; // 3 hours ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('3 hours ago');
    });

    it('should return "days ago" with count for times more than 24 hours ago', () => {
      const dateString = '2024-01-13T12:00:00.000Z'; // 2 days ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('2 days ago');
    });

    it('should handle custom time reference as Date object', () => {
      const dateString = '2024-01-15T11:30:00.000Z';
      const customTimeRef = new Date('2024-01-15T12:30:00.000Z'); // 1 hour later

      const result = DateHelper.formatRelativeTime(dateString, customTimeRef);

      expect(result).toEqual('1 hour ago');
    });

    it('should handle custom time reference as string', () => {
      const dateString = '2024-01-15T11:30:00.000Z';
      const customTimeRef = '2024-01-15T12:30:00.000Z'; // 1 hour later

      const result = DateHelper.formatRelativeTime(dateString, customTimeRef);

      expect(result).toEqual('1 hour ago');
    });

    it('should handle edge case of exactly 5 seconds ago', () => {
      const dateString = '2024-01-15T11:59:55.000Z'; // Exactly 5 seconds ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('less than a minute ago');
    });

    it('should handle edge case of exactly 1 minute ago', () => {
      const dateString = '2024-01-15T11:59:00.000Z'; // Exactly 1 minute ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('1 minute ago');
    });

    it('should handle edge case of exactly 1 hour ago', () => {
      const dateString = '2024-01-15T11:00:00.000Z'; // Exactly 1 hour ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('1 hour ago');
    });

    it('should handle edge case of exactly 24 hours ago', () => {
      const dateString = '2024-01-14T12:00:00.000Z'; // Exactly 24 hours ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('1 day ago');
    });

    it('should handle future dates (negative time difference)', () => {
      const dateString = '2024-01-15T12:30:00.000Z'; // 30 minutes in the future

      const result = DateHelper.formatRelativeTime(dateString);

      // Should still return "just now" for future dates
      expect(result).toEqual('just now');
    });

    it('should handle very old dates', () => {
      const dateString = '2020-01-15T12:00:00.000Z'; // 4 years ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('1461 days ago'); // ~4 years in days
    });

    it('should handle invalid date strings gracefully', () => {
      const invalidDateString = 'invalid-date';

      const result = DateHelper.formatRelativeTime(invalidDateString);

      // Should return "" for invalid dates (NaN comparison)
      expect(result).toEqual('');
    });

    it('should handle empty string date', () => {
      const emptyDateString = '';

      const result = DateHelper.formatRelativeTime(emptyDateString);

      // Should return "" for empty dates
      expect(result).toEqual('');
    });
  });
});
