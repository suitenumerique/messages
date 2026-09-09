import { useCallback, useState } from "react"
import { Button, ButtonProps, Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { openPicker, type Item, type PickerResult } from "@gouvfr-lasuite/drive-sdk";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { openNativeDrivePicker } from "@/features/native/drive-picker";
import { isNativePlatform } from "@/features/native/platform";
import { useConfig } from "@/features/providers/config";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { DriveIcon } from "./drive-icon";
import { Attachment } from "@/features/api/gen/models/attachment";
import { handle } from "@/features/utils/errors";

export type DriveFile = { id: string, url: string } & Omit<Attachment, 'sha256' | 'blobId' | 'cid'>;

type DriveAttachmentPickerProps = ButtonProps & {
    onPick: (attachments: DriveFile[]) => void;
}

// TODO: Remove this type once the Drive SDK is updated to include mimetype
type PatchedItem = Item & { mimetype?: string };

const serializeToDriveFile = (item: PatchedItem): DriveFile => ({
    id: item.id,
    name: item.title,
    url: item.url_permalink ?? item.url,
    type: item.mimetype || "application/octet-stream",
    size: item.size,
    created_at: new Date().toISOString(),
});

/**
 * Picking files from the configured Drive instance, shared by the
 * attachment uploader button and the mobile toolbar's "insert a file" menu.
 *
 * Drive Config is retrieved from the backend. Take a look at the
 * `DRIVE_CONFIG` in the `settings.py` file in the backend.
 *
 * https://github.com/suitenumerique/drive
 */
export const useDrivePicker = () => {
    const [isLoading, setIsLoading] = useState(false);
    const config = useConfig();
    const isDriveDisabled = !useFeatureFlag(FEATURE_KEYS.DRIVE);

    const pick = useCallback(async (): Promise<DriveFile[]> => {
        if (isDriveDisabled) return [];
        setIsLoading(true);
        let result: PickerResult | null = null;

        try {
            const pickerConfig = {
                url: config.DRIVE!.sdk_url,
                apiUrl: config.DRIVE!.api_url,
            };
            // The SDK's picker popup breaks inside the Capacitor shell (system
            // browser, suspended poll) — see openNativeDrivePicker.
            result = isNativePlatform()
                ? await openNativeDrivePicker(pickerConfig)
                : await openPicker(pickerConfig);
        } catch (error) {
            handle(new Error("Failed to open picker."), { extra: { error } });
        } finally {
            setIsLoading(false);
        }

        if (result?.type === "picked" && result.items) {
            return (result.items as PatchedItem[]).map(serializeToDriveFile);
        }
        return [];
    }, [isDriveDisabled]);

    return {
        isAvailable: !isDriveDisabled,
        isLoading,
        appName: config.DRIVE?.app_name,
        pick,
    };
};

/**
 * DriveAttachmentPicker is a component that allows the user to pick files
 * from a Drive instance if one is configured otherwise it will return null.
 */
export const DriveAttachmentPicker = ({ onPick, ...buttonProps }: DriveAttachmentPickerProps) => {
    const { t } = useTranslation();
    const config = useConfig();
    const { isAvailable, isLoading, pick } = useDrivePicker();

    const handlePick = useCallback(async () => {
        const files = await pick();
        if (files.length > 0) onPick(files);
    }, [pick, onPick]);

    if (!isAvailable) return null;

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
                onClick={handlePick}
                className="drive-attachment-picker"
            />
        </Tooltip>
    )
}
