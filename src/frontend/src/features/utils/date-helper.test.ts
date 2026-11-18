import { DateHelper } from './date-helper';
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';

describe('DateHelper', () => {
  // Mock current date to 2025-04-17
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-04-17T16:00:00'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('formatDate', () => {
    it.each(['', 'garbage', undefined as unknown as string])(
      'returns an empty string for an invalid date (%s) instead of throwing',
      (value) => {
        expect(DateHelper.formatDate(value, 'en')).toBe('');
        expect(DateHelper.formatEventTimestamp(value, 'en')).toBe('');
      },
    );

    it('should format time when date is today', () => {
      const todayDate = '2025-04-17T17:30:00';
      expect(DateHelper.formatDate(todayDate, 'fr')).toBe('17:30');
      expect(DateHelper.formatDate(todayDate, 'en')).toBe('17:30');
    });

    it('should format time when date is today and showTime is true', () => {
      const todayDate = '2025-04-17T17:30:00';
      expect(DateHelper.formatDate(todayDate, 'fr', true)).toBe('17:30');
      expect(DateHelper.formatDate(todayDate, 'en', true)).toBe('17:30');
    });

    it('should format as "Today" when date is today and showTime is false', () => {
      const todayDate = '2025-04-17T17:30:00';
      expect(DateHelper.formatDate(todayDate, 'en', false)).toBe('Today');
    });

    it('should format as "Yesterday" when date is yesterday', () => {
      const yesterdayDate = '2025-04-16T15:30:00';
      expect(DateHelper.formatDate(yesterdayDate, 'en')).toBe('Yesterday');
    });

    it('should format as day name when date is in the same week but not today/yesterday', () => {
      const sameWeekDate = '2025-04-14T15:30:00'; // Monday
      expect(DateHelper.formatDate(sameWeekDate, 'fr')).toBe('lundi');
      expect(DateHelper.formatDate(sameWeekDate, 'en')).toBe('Monday');
    });

    it('should format as day month when date is in the same year but not same week', () => {
      const sameYearDate = '2025-02-20T15:30:00';
      expect(DateHelper.formatDate(sameYearDate, 'fr')).toBe('20 févr.');
      expect(DateHelper.formatDate(sameYearDate, 'en')).toBe('20 Feb');
    });

    it('should format as full date when more than 1 year', () => {
      const oldDate = '2024-04-15T15:30:00';
      expect(DateHelper.formatDate(oldDate, 'fr')).toBe('15/04/2024');
      expect(DateHelper.formatDate(oldDate, 'en')).toBe('15/04/2024');
    });
  });

  describe('formatWallClock', () => {
    it('formats the date and time according to the locale', () => {
      expect(DateHelper.formatWallClock('2026-10-20T18:00', 'en-US')).toBe('Oct 20, 2026, 6:00 PM');
      expect(DateHelper.formatWallClock('2026-10-20T18:00:00', 'fr-FR')).toBe('20 oct. 2026, 18:00');
    });

    it("shows the time as written, even in a skipped hour of the browser's zone", () => {
      const originalTz = process.env.TZ;
      try {
        // 2026-03-08 02:30 does not exist in New York: a local Date reads 03:30.
        process.env.TZ = 'America/New_York';
        expect(new Date('2026-03-08T02:30').getHours()).toBe(3);
        expect(DateHelper.formatWallClock('2026-03-08T02:30', 'en-US')).toBe('Mar 8, 2026, 2:30 AM');
      } finally {
        if (originalTz === undefined) delete process.env.TZ;
        else process.env.TZ = originalTz;
      }
    });

    it('returns undefined for an invalid date', () => {
      expect(DateHelper.formatWallClock('20261020T1800', 'en-US')).toBeUndefined();
    });
  });

  describe('fromWallClock', () => {
    // Python: datetime(..., tzinfo=ZoneInfo("Europe/Paris")).astimezone(UTC)
    it.each([
      ['2026-06-01T10:00', 'Europe/Paris', '2026-06-01T08:00:00.000Z'],
      ['2026-06-01T10:00:30', 'UTC', '2026-06-01T10:00:30.000Z'],
      // Repeated hour (fold=0): the first 02:30 (CEST).
      ['2026-10-25T02:30', 'Europe/Paris', '2026-10-25T00:30:00.000Z'],
      // Skipped hour (fold=0): offset from before the change (CET).
      ['2026-03-29T02:30', 'Europe/Paris', '2026-03-29T01:30:00.000Z'],
      ['2026-03-08T02:30', 'America/New_York', '2026-03-08T07:30:00.000Z'],
    ])('reads %s in %s like the backend', (wall, tz, expected) => {
      expect(new Date(DateHelper.fromWallClock(wall, tz)).toISOString()).toBe(expected);
    });

    it('is NaN for an invalid time or zone', () => {
      expect(DateHelper.fromWallClock('garbage', 'UTC')).toBeNaN();
      expect(DateHelper.fromWallClock('2026-06-01T10:00', 'Mars/Olympus')).toBeNaN();
    });

    it.each([
      ['0026-06-01T10:00', '0026-06-01T10:00:00.000Z'],
      ['0099-06-01T10:00', '0099-06-01T10:00:00.000Z'],
      ['0500-06-01T10:00', '0500-06-01T10:00:00.000Z'],
    ])('reads the year of %s as written', (wall, expected) => {
      expect(new Date(DateHelper.fromWallClock(wall, 'UTC')).toISOString()).toBe(expected);
    });
  });

  describe('isSameTimeZone', () => {
    it('matches aliases of a zone', () => {
      expect(DateHelper.isSameTimeZone('Asia/Kolkata', 'Asia/Calcutta')).toBe(true);
      expect(DateHelper.isSameTimeZone('UTC', 'Etc/UTC')).toBe(true);
      expect(DateHelper.isSameTimeZone('Europe/Paris', 'Europe/Berlin')).toBe(false);
      expect(DateHelper.isSameTimeZone('Mars/Olympus', 'Mars/Olympus')).toBe(true);
    });
  });

  describe('offsetInstant', () => {
    it.each([
      ['2026-06-01T10:00:00Z', '2026-06-01T10:00:00.000Z'],
      ['2026-06-01T10:00:00+02:00', '2026-06-01T08:00:00.000Z'],
      ['2026-06-01T10:00:00-0130', '2026-06-01T11:30:00.000Z'],
      // Python writes sub-minute offsets with seconds; Date.parse refuses them.
      ['2026-06-01T10:00:00+01:00:30', '2026-06-01T08:59:30.000Z'],
      ['2026-06-01T10:00:00.250-00:00:30', '2026-06-01T10:00:30.250Z'],
    ])('reads %s', (value, expected) => {
      expect(DateHelper.hasOffset(value)).toBe(true);
      expect(new Date(DateHelper.offsetInstant(value)).toISOString()).toBe(expected);
    });

    it.each(['2026-06-01T10:00', '2026-06-01T10:00:00', '2026-06-01'])('finds no offset in %s', (value) => {
      expect(DateHelper.hasOffset(value)).toBe(false);
      expect(DateHelper.offsetInstant(value)).toBeNaN();
    });
  });

  describe('compareInstants', () => {
    it('compares below the millisecond', () => {
      expect(DateHelper.compareInstants('2026-06-01T10:00:00.123456Z', '2026-06-01T10:00:00.123455Z')).toBeGreaterThan(0);
      expect(DateHelper.compareInstants('2026-06-01T10:00:00.123Z', '2026-06-01T10:00:00.123001Z')).toBeLessThan(0);
      expect(DateHelper.compareInstants('2026-06-01T10:00:00.1235Z', '2026-06-01T10:00:00.1234Z')).toBeGreaterThan(0);
    });

    it('compares instants, not strings', () => {
      expect(DateHelper.compareInstants('2026-06-01T10:00:00Z', '2026-06-01T10:00:00.000000Z')).toBe(0);
      expect(DateHelper.compareInstants('2026-06-01T12:00:00+02:00', '2026-06-01T10:00:00Z')).toBe(0);
      expect(DateHelper.compareInstants('2026-06-01T10:00:01Z', '2026-06-01T10:00:00.999999Z')).toBeGreaterThan(0);
    });

    it('is NaN for an unreadable value', () => {
      expect(DateHelper.compareInstants('garbage', '2026-06-01T10:00:00Z')).toBeNaN();
    });
  });

  describe('resolveTimeZone', () => {
    it('keeps a known time zone', () => {
      expect(DateHelper.resolveTimeZone('Europe/Paris')).toBe('Europe/Paris');
      expect(DateHelper.resolveTimeZone('UTC')).toBe('UTC');
    });

    it.each([undefined, null, '', 0, false, ['Europe/Paris'], 'Mars/Olympus'])(
      'falls back to UTC for %s, as the backend does',
      (value) => {
        expect(DateHelper.resolveTimeZone(value)).toBe('UTC');
      },
    );
  });

  describe('toWallClock', () => {
    it('keeps a date-time without offset as is', () => {
      expect(DateHelper.toWallClock('2026-10-20T18:00', 'Europe/Paris')).toBe('2026-10-20T18:00');
    });

    it('converts a date-time with offset into the time zone', () => {
      expect(DateHelper.toWallClock('2026-10-20T18:00:00+00:00', 'Europe/Paris')).toBe('2026-10-20T20:00:00');
      expect(DateHelper.toWallClock('2026-10-20T22:30:00Z', 'Europe/Paris')).toBe('2026-10-21T00:30:00');
      expect(DateHelper.toWallClock('2026-10-20T20:00:00+02:00', 'UTC')).toBe('2026-10-20T18:00:00');
    });

    it('returns undefined for an invalid date or time zone', () => {
      expect(DateHelper.toWallClock('not a dateZ', 'UTC')).toBeUndefined();
      expect(DateHelper.toWallClock('2026-10-20T18:00:00Z', 'Mars/Olympus')).toBeUndefined();
    });

    it('writes years before 1000 with four digits', () => {
      expect(DateHelper.toWallClock('0500-01-01T00:00:00+00:00', 'UTC')).toBe('0500-01-01T00:00:00');
      expect(DateHelper.toWallClock('0001-01-01T12:00:00Z', 'UTC')).toBe('0001-01-01T12:00:00');
    });

    it('returns undefined before year 1, which the backend cannot represent', () => {
      expect(DateHelper.toWallClock('0001-01-01T00:00:00+01:00', 'UTC')).toBeUndefined();
    });

    it('converts an offset with seconds', () => {
      expect(DateHelper.toWallClock('2026-06-01T10:00:00+01:00:30', 'UTC')).toBe('2026-06-01T08:59:30');
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

    it('should return "days ago" with count for times between 24 hours and 7 days ago', () => {
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

    it('should return "weeks ago" with count for times between 7 and 30 days ago', () => {
      const dateString = '2024-01-01T12:00:00.000Z'; // 14 days ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('2 weeks ago');
    });

    it('should handle edge case of exactly 7 days ago', () => {
      const dateString = '2024-01-08T12:00:00.000Z'; // Exactly 7 days ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('1 week ago');
    });

    it('should return "months ago" with count for times between 30 and 365 days ago', () => {
      const dateString = '2023-11-15T12:00:00.000Z'; // ~2 months ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('2 months ago');
    });

    it('should handle edge case of exactly 30 days ago', () => {
      const dateString = '2023-12-16T12:00:00.000Z'; // Exactly 30 days ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('1 month ago');
    });

    it('should return "years ago" with count for times more than 365 days ago', () => {
      const dateString = '2022-01-15T12:00:00.000Z'; // 2 years ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('2 years ago');
    });

    it('should handle edge case of exactly 365 days ago', () => {
      const dateString = '2023-01-15T12:00:00.000Z'; // Exactly 365 days ago

      const result = DateHelper.formatRelativeTime(dateString);

      expect(result).toEqual('1 year ago');
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

      expect(result).toEqual('4 years ago');
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
