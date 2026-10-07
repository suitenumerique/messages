import type { StorageEntitlement } from "@/features/api/gen/models/storage_entitlement";

type LimitedStorageEntitlement = StorageEntitlement & { max_storage: number };

/**
 * Helper class for storage quota operations.
 */
export class QuotaHelper {
    // Decimal (SI) gigabyte, as the backend sizes the quotas (e.g. 5 * 1000**3).
    private static readonly BYTES_PER_GB = 1000 ** 3;

    /**
     * Whether a storage level has a limit to gauge against.
     * @param level - The storage entitlement to check, null when the level does not apply.
     * @returns false when the limit is unknown (null) or unlimited (0).
     */
    static hasLimit(level: StorageEntitlement | null): level is LimitedStorageEntitlement {
        return !!level && level.max_storage != null && level.max_storage > 0;
    }

    /**
     * Whether a storage level has used up its allowance.
     * @param level - The storage entitlement to check, null when the level does not apply.
     * @returns false when there is no limit to reach.
     */
    static isQuotaReached(level: StorageEntitlement | null) {
        return QuotaHelper.hasLimit(level) && level.storage_used >= level.max_storage;
    }

    /**
     * Convert a storage size to gigabytes.
     * @param bytes - The size in bytes.
     * @returns The size in decimal gigabytes (1 GB = 1000³ bytes).
     */
    static toGigabytes(bytes: number) {
        return bytes / QuotaHelper.BYTES_PER_GB;
    }
}

export default QuotaHelper;
