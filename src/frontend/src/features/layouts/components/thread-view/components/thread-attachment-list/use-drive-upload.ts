import { useEffect, useRef, useState } from "react";
import { useThirdPartyDriveCreate } from "@/features/api/gen";
import { handle } from "@/features/utils/errors";
import { driveUploadStore } from "./drive-upload-store";

export type DriveUploadState = "idle" | "uploading" | "error" | "success";

/**
 * Owns the lifecycle of saving a blob attachment to the user's Drive
 * workspace: trigger the upload, cache the resulting Drive file id, and
 * expose it for an "Open in Drive" action. Shared between the compact
 * button in the thread attachment list and the labelled action in the
 * preview sidebar.
 */
export function useDriveUpload(blobId: string) {
    const [state, setState] = useState<DriveUploadState>("idle");
    const [driveFileId, setDriveFileId] = useState<string | undefined>(
        () => driveUploadStore.get(blobId),
    );

    // The preview modal keeps a single sidebar mounted while the user navigates
    // between files, so this hook instance is reused with different blobIds.
    // Re-seed the cached Drive state from the store on change, otherwise we leak
    // the previous file's id/state. Reset during render (per React guidance) so
    // the new blob never paints with stale values.
    const [trackedBlobId, setTrackedBlobId] = useState(blobId);
    // Lets the async upload tell whether the displayed file is still the one it
    // started for; the blobId captured in its closure can't see later changes.
    const latestBlobIdRef = useRef(blobId);
    if (blobId !== trackedBlobId) {
        setTrackedBlobId(blobId);
        latestBlobIdRef.current = blobId;
        setDriveFileId(driveUploadStore.get(blobId));
        setState("idle");
    }

    const uploadToDrive = useThirdPartyDriveCreate({
        request: { logoutOn401: false },
    });

    const upload = async () => {
        if (state === "uploading") return;
        // The user can navigate to another file while this upload is in flight,
        // swapping blobId under us, so pin the target for the whole call.
        const uploadingBlobId = blobId;
        setState("uploading");
        try {
            const { data } = await uploadToDrive.mutateAsync({
                data: { blob_id: uploadingBlobId },
            });
            // The store is keyed by blobId, so caching the result is always safe.
            driveUploadStore.set(uploadingBlobId, data.id);
            // ...but only paint the id/state if we're still showing that file.
            if (latestBlobIdRef.current !== uploadingBlobId) return;
            setDriveFileId(data.id);
            setState("success");
        } catch (error) {
            handle(error);
            if (latestBlobIdRef.current !== uploadingBlobId) return;
            setState("error");
        }
    };

    // Auto-clear transient states so a follow-up retry isn't blocked by
    // a stale flag. Error stays longer than success: gives the user time
    // to read the failure before going back to idle.
    useEffect(() => {
        if (state === "error" || state === "success") {
            const timeoutId = setTimeout(
                () => setState("idle"),
                state === "success" ? 1500 : 5000,
            );
            return () => clearTimeout(timeoutId);
        }
    }, [state]);

    return { state, driveFileId, upload };
}
