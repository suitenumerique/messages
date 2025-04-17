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
}); 