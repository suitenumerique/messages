import { useState, useMemo, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useThirdPartyDriveRetrieve, useThirdPartyDriveCreate } from "@/features/api/gen";
import { Attachment } from "@/features/api/gen/models";
import usePrevious from "@/hooks/use-previous";
import { Spinner, Icon } from "@gouvfr-lasuite/ui-kit";
import { Tooltip, Button } from "@openfun/cunningham-react";
import clsx from "clsx";
import { DrivePreviewLink } from "./drive-preview-link";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { useConfig } from "@/features/providers/config";
import { handle } from "@/features/utils/errors";


type DriveUploadButtonProps = {
    attachment: Attachment;
}

/**
 * UploadDriveButton
 * Button to upload an attachment to the Drive personal workspace of the user.
 * At mount, it checks if the file is already in the Drive personal workspace and display the preview link if it is.
 * If the file is not in the Drive personal workspace, it displays the upload button.
 *
 * To prevent to check if the file already exists too often, the query is cached for 10 minutes.
 */
export const DriveUploadButton = ({ attachment }: DriveUploadButtonProps) => {
    const { t } = useTranslation();
    const { DRIVE } = useConfig();
    const isDriveDisabled = !useFeatureFlag(FEATURE_KEYS.DRIVE);
    const [state, setState] = useState<'idle' | 'uploading' | 'error' | 'success'>('idle');
    const [driveFileId, setDriveFileId] = useState<string | null>(null);
    const prevState = usePrevious(state);
    const queryClient = useQueryClient();
    const driveFilesQuery = useThirdPartyDriveRetrieve({
        title: attachment.name,
    }, {
        query: {
            enabled: !isDriveDisabled && !driveFileId,
            // Keep data fresh for 10 minutes to prevent requests each time the component is rendered.
            staleTime: 600000,
            refetchOnReconnect: false,
        },
        request: {
            logoutOn401: false,
        },
    });
    const uploadToDrive = useThirdPartyDriveCreate({
        request: {
            logoutOn401: false,
        },
        mutation: {
            onSuccess: (data) => {
                setDriveFileId(data.data.id);
                // Update the drive files query data to include the new file and prevent a new request.
                queryClient.setQueryData(driveFilesQuery.queryKey, (cachedData) => {
                    const previousData = cachedData ?? { status: 200, headers: new Headers(), data: { next: null, previous: null, count: 0, results: [] } };
                    return {
                        ...previousData,
                        data: {
                            ...previousData.data,
                            count: previousData.data.count + 1,
                            results: [...previousData.data.results, data.data!],
                        }
                    }
                });
            },
        },
    });
    const showUploadTooltip = useMemo(() => ['success', 'error'].includes(state), [state]);
    const isBusy = state === 'uploading' || driveFilesQuery.isLoading;

    const handleUploadToDrive = async () => {
        if (isBusy) return;
        setState('uploading');
        try {
            await uploadToDrive.mutateAsync({
                data: {
                    blob_id: attachment.blobId,
                }
            });
            setState('success');
        } catch (error) {
            handle(error);
            setState('error');
        }
    }

    const StateIcon = useMemo(() => {
        if (isBusy) return <Spinner size="sm" />;
        if (state === 'success') return <Icon name="check" />;
        if (state === 'error') return <Icon name="error" />;
        return <Icon name="drive_folder_upload" />;
    }, [state, driveFilesQuery.isLoading]);

    useEffect(() => {
        if (['error', 'success'].includes(state)) {
            const timeoutId = setTimeout(() => {
                setState('idle');
            }, state === 'success' ? 1500 : 5000);
            return () => clearTimeout(timeoutId);
        }
    }, [state]);

    useEffect(() => {
        const driveFiles = driveFilesQuery.data?.data?.results ?? [];
        if (!driveFileId && driveFiles.length > 0) {
            const file = driveFiles.find((file) =>
                file.filename === attachment.name
                && file.mimetype === attachment.type
                && file.size === attachment.size
            );
            if (file) {
                setDriveFileId(file.id);
            }
        }
    }, [driveFilesQuery.data?.data?.results?.length, driveFileId]);

    if (isDriveDisabled) return null;

    return (
        <div className="attachment-item-drive-upload-button-container">
            {(driveFileId && state === 'idle') ? <DrivePreviewLink fileId={driveFileId} /> : (
                <Tooltip content={t("Save into your {{driveAppName}}'s workspace", { driveAppName: DRIVE.app_name })}>
                    <Button
                        aria-label={t("Save into your {{driveAppName}}'s workspace", { driveAppName: DRIVE.app_name })}
                        size="medium"
                        icon={StateIcon}
                        disabled={isBusy || state !== 'idle'}
                        aria-busy={isBusy}
                        color={state == 'error' ? 'danger' : 'tertiary-text'}
                        onClick={handleUploadToDrive}
                    />
                </Tooltip>
            )}
            <div
                className={clsx(
                    "attachment-item--drive-upload-tooltip",
                    {
                        "attachment-item--drive-upload-tooltip--visible": showUploadTooltip,
                        "attachment-item--drive-upload-tooltip--error": state === 'error',
                    })}
                aria-live="polite"
                aria-hidden={!showUploadTooltip}
            >
                {(state === 'success' || prevState === 'success') && t("Attachment saved into your {{driveAppName}}'s workspace.", { driveAppName: DRIVE.app_name })}
                {(state === 'error' || prevState === 'error') && t("Attachment failed to be saved into your {{driveAppName}}'s workspace.", { driveAppName: DRIVE.app_name })}
            </div>
        </div>
    )
}

