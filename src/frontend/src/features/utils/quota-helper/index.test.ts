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

  describe('getGaugedLevel', () => {
    const limited = { storage_used: 1, max_storage: 5 };
    const organization = { storage_used: 10, max_storage: 100 };

    it('should be null while the entitlements are not loaded', () => {
      expect(QuotaHelper.getGaugedLevel(undefined)).toBeNull();
    });

    it('should prefer the account limit over the organization one', () => {
      expect(QuotaHelper.getGaugedLevel({ account: limited, organization })).toBe(limited);
    });

    it.each([
      ['unknown', null],
      ['unlimited', 0],
    ])('should fall back on the organization when the account limit is %s', (_, max_storage) => {
      const account = { storage_used: 0, max_storage };
      expect(QuotaHelper.getGaugedLevel({ account, organization })).toBe(organization);
    });

    it('should be null when no level has a limit', () => {
      const account = { storage_used: 0, max_storage: 0 };
      expect(QuotaHelper.getGaugedLevel({ account, organization: null })).toBeNull();
      expect(QuotaHelper.getGaugedLevel({ account, organization: account })).toBeNull();
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
