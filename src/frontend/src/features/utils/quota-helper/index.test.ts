import { describe, it, expect } from 'vitest';
import { QuotaHelper } from './index';

describe('QuotaHelper', () => {
  describe('hasLimit', () => {
    it('should be false when the level does not apply', () => {
      expect(QuotaHelper.hasLimit(null)).toBe(false);
    });

    it('should be false when the limit is unknown or unlimited', () => {
      expect(QuotaHelper.hasLimit({ storage_used: 10, max_storage: null })).toBe(false);
      expect(QuotaHelper.hasLimit({ storage_used: 10, max_storage: 0 })).toBe(false);
    });

    it('should be true when the limit is positive', () => {
      expect(QuotaHelper.hasLimit({ storage_used: 0, max_storage: 1 })).toBe(true);
    });
  });

  describe('toGigabytes', () => {
    it('should convert bytes to decimal gigabytes', () => {
      expect(QuotaHelper.toGigabytes(0)).toBe(0);
      expect(QuotaHelper.toGigabytes(5 * 1000 ** 3)).toBe(5);
      expect(QuotaHelper.toGigabytes(1500 * 1000 ** 2)).toBe(1.5);
    });
  });

  describe('isQuotaReached', () => {
    it('should be false when the level does not apply', () => {
      expect(QuotaHelper.isQuotaReached(null)).toBe(false);
    });

    it('should be false when the limit is unknown or unlimited', () => {
      expect(QuotaHelper.isQuotaReached({ storage_used: 10, max_storage: null })).toBe(false);
      expect(QuotaHelper.isQuotaReached({ storage_used: 10, max_storage: 0 })).toBe(false);
    });

    it('should be false while usage stays below the limit', () => {
      expect(QuotaHelper.isQuotaReached({ storage_used: 99, max_storage: 100 })).toBe(false);
    });

    it('should be true once usage reaches or exceeds the limit', () => {
      expect(QuotaHelper.isQuotaReached({ storage_used: 100, max_storage: 100 })).toBe(true);
      expect(QuotaHelper.isQuotaReached({ storage_used: 150, max_storage: 100 })).toBe(true);
    });
  });
});
