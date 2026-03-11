import { useCallback, useState } from "react"
import { Button, ButtonProps, Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { openPicker, type Item, type PickerResult } from "@gouvfr-lasuite/drive-sdk";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useConfig } from "@/features/providers/config";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { DriveIcon } from "./drive-icon";
import { Attachment } from "@/features/api/gen/models/attachment";
import { handle } from "@/features/utils/errors";

export type DriveFile = { id: string, url: string } & Omit<Attachment, 'sha256' | 'blobId' | 'cid'>;

type DriveAttachmentPickerProps = ButtonProps & {
    onPick: (attachments: DriveFile[]) => void;
}

// TODO: Remove this type once the Drive SDK is updated to include the url_permalink field
type PatchedItem = Item & { url_permalink: string };

/**
 * DriveAttachmentPicker is a component that allows the user to pick files
 * from a Drive instance if one is configured otherwise it will return null.
 *
 * Drive Config is retrieved from the backend. Take a look at the `DRIVE_CONFIG`
 * in the `settings.py` file in the backend.
 *
 * https://github.com/suitenumerique/drive
 */
export const DriveAttachmentPicker = ({ onPick, ...buttonProps }: DriveAttachmentPickerProps) => {
    const { t } = useTranslation();
    const [isLoading, setIsLoading] = useState(false);
    const config = useConfig();
    const isDriveDisabled = !useFeatureFlag(FEATURE_KEYS.DRIVE);
    const serializeToDriveFile = (item: PatchedItem): DriveFile => ({
        id: item.id,
        name: item.title,
        url: item.url_permalink ?? item.url,
        type: item.type,
        size: item.size,
        created_at: new Date().toISOString(),
    });

    const pick = useCallback(async () => {
        if (isDriveDisabled) return;
        setIsLoading(true);
        let result: PickerResult | null = null;

        try {
            result = await openPicker({
                url: config.DRIVE!.sdk_url,
                apiUrl: config.DRIVE!.api_url,
            });
        } catch (error) {
            handle(new Error("Failed to open picker."), { extra: { error } });
        } finally {
            setIsLoading(false);
        }

        if (result?.type === "picked" && result.items) {
            onPick((result.items as PatchedItem[]).map(serializeToDriveFile));
        }
    }, [isDriveDisabled]);

    if (isDriveDisabled) return null;

    return (
        <Tooltip content={t('Add attachment from {{driveAppName}}', { driveAppName: config.DRIVE.app_name })}>
            <Button
                aria-label={t('Add attachment from {{driveAppName}}')}
                {...buttonProps}
                variant="secondary"
                icon={isLoading ? <Spinner size="sm" /> : <DriveIcon />}
                type="button"
                disabled={isLoading || buttonProps.disabled}
                aria-busy={isLoading}
                onClick={pick}
                className="drive-attachment-picker"
            />
        </Tooltip>
    )
}
