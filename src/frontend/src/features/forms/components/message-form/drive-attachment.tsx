import { SVGProps, useCallback } from "react"
import { DriveFile } from "@/pages/drive-selection";
import { Button, Tooltip } from "@openfun/cunningham-react"
import { openPicker, type Item } from "@gouvfr-lasuite/drive-sdk";
import { useTranslation } from "react-i18next";

type DriveAttachmentProps = {
    onChange: (attachments: DriveFile[]) => void;
}

export const DriveAttachment = ({ onChange }: DriveAttachmentProps) => {
    const { t } = useTranslation();

    const pick = useCallback(async () => {
        const { items } = await openPicker({
            url: "http://localhost:3001/sdk",
        });

        onChange(items.map((item: Item) => ({
            id: item.id,
            name: item.title,
            url: item.url,
            type: item.mimetype,
            size: item.size,
            created_at: new Date().toISOString(),
        })));
    }, []);
    
    return (
        <Tooltip content={t('tooltips.add_attachment_from_drive')}>
        <Button
            color="tertiary"
            icon={<DriveIcon />}
            type="button"
            onClick={pick}
            />
        </Tooltip>
    )
}

export const DriveIcon = ({ size = 'small', ...props }: { size?: 'small' | 'medium' | 'large' } & SVGProps<SVGSVGElement>) => {
    const sizeMap = {
        small: 21,
        medium: 32,
        large: 48,
    }
    return (
        <svg width={sizeMap[size]} height={sizeMap[size]} viewBox="0 0 64 65" fill="none" xmlns="http://www.w3.org/2000/svg" {...props}>
            <path d="M55.3531 19.9525H16.8394C12.465 19.9525 8.06606 23.3947 7.01421 27.6408L4 39.8087V16.1976C4 13.6559 4.58282 11.7415 5.74845 10.4546C6.92902 9.15152 8.56539 8.5 10.6575 8.5H17.0013C17.7783 8.5 18.4508 8.5563 19.0187 8.66891C19.5866 8.78152 20.1096 8.97457 20.5878 9.24805C21.066 9.50544 21.5666 9.8674 22.0897 10.3339L23.3674 11.4681C23.995 11.9989 24.5853 12.377 25.1383 12.6022C25.6912 12.8274 26.3711 12.94 27.1781 12.94H48.025C50.4309 12.94 52.2541 13.6076 53.4945 14.9428C54.607 16.1262 55.2265 17.7961 55.3531 19.9525Z" fill="currentColor"/>
            <path d="M11.3531 54.5C8.93219 54.5 7.27319 53.8071 6.37613 52.4213C5.47493 51.0522 5.3552 49.032 6.01696 46.3606L10.6542 27.6409C11.2228 25.3457 13.6005 23.4851 15.9651 23.4851H58.7796C61.1442 23.4851 62.6002 25.3457 62.0316 27.6409L57.3944 46.3606C56.7326 49.032 55.6344 51.0522 54.0997 52.4213C52.5609 53.8071 50.723 54.5 48.586 54.5H11.3531Z" fill="currentColor"/>
        </svg>
    )
}
