/**
 * In-memory store that maps attachment blobIds to their Drive file IDs.
 * Persists across thread navigations (component mount/unmount cycles)
 * so the user sees the Drive preview link without re-uploading.
 */
const driveFileIds = new Map<string, string>();

export const driveUploadStore = {
    get: (blobId: string) => driveFileIds.get(blobId),
    set: (blobId: string, driveFileId: string) => {
        driveFileIds.set(blobId, driveFileId);
    },
};
