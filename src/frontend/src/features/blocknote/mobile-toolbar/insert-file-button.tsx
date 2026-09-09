import { useBlockNoteEditor, useEditorState } from "@blocknote/react";
import { IconSize } from "@gouvfr-lasuite/ui-kit";
import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import {
    DriveFile,
    useDrivePicker,
} from "@/features/forms/components/message-form/drive-attachment-picker";
import { DriveIcon } from "@/features/forms/components/message-form/drive-icon";
import { cursorHasInlineContent, insertImageBlock } from "../image-upload-button";
import { MobileToolbarButton } from "./buttons";
import { useKeepEditorFocusOnTap } from "./hooks";
import { AttachFile, Picture } from "@gouvfr-lasuite/ui-kit/icons";
import { Icon } from "@/features/ui/components/icon";

const MENU_MIN_WIDTH = 240;
const VIEWPORT_MARGIN = 8;

type InsertFileButtonProps = {
    /**
     * Adds files to the message as attachments. Absent in the composers that
     * have no attachment concept (signature / template editors), where the
     * button degrades to a plain "insert image" action.
     */
    onAttachFiles?: (files: File[]) => Promise<void> | void;
    /**
     * Adds files picked from the configured Drive instance as attachments.
     * The menu entry only shows when this is provided AND Drive is enabled.
     */
    onDriveAttachmentPick?: (files: DriveFile[]) => void;
};

/**
 * The "insert a file" toolbar option: a popover menu choosing between an
 * inline image and a plain attachment. Portaled to <body> because the
 * toolbar row is a horizontal scroller whose overflow would clip anything
 * anchored inside it; the coordinates are measured from the button on open
 * (the bar is position: fixed with no transform, so viewport coordinates
 * are stable while the menu is up).
 */
export const InsertFileButton = ({
    onAttachFiles,
    onDriveAttachmentPick,
}: InsertFileButtonProps) => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor();
    // Same guard as the toolbar bar itself: taps on the menu must not steal
    // the DOM focus, or the keyboard would fold and tear the toolbar down.
    const keepEditorFocusOnTap = useKeepEditorFocusOnTap();
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [menuPosition, setMenuPosition] = useState<{
        left: number;
        bottom: number;
    } | null>(null);
    const drivePicker = useDrivePicker();
    const canPickFromDrive = !!onDriveAttachmentPick && drivePicker.isAvailable;

    const canInsertImage = useEditorState({
        editor,
        selector: ({ editor }) =>
            "image" in editor.schema.blockSpecs && cursorHasInlineContent(editor),
    });

    const closeMenu = () => setMenuPosition(null);

    const openMenu = (event: React.MouseEvent<HTMLButtonElement>) => {
        const rect = event.currentTarget.getBoundingClientRect();
        setMenuPosition({
            left: Math.max(
                VIEWPORT_MARGIN,
                Math.min(
                    rect.left,
                    window.innerWidth - MENU_MIN_WIDTH - VIEWPORT_MARGIN,
                ),
            ),
            bottom: window.innerHeight - rect.top + VIEWPORT_MARGIN,
        });
    };

    if (!onAttachFiles && !canPickFromDrive) {
        if (!canInsertImage) return null;
        return (
            <MobileToolbarButton
                icon={<Icon icon={Picture} size={IconSize.MEDIUM} />}
                label={t("Insert image")}
                onClick={() => insertImageBlock(editor)}
            />
        );
    }

    return (
        <>
            <MobileToolbarButton
                icon={<Icon icon={AttachFile} size={IconSize.MEDIUM} />}
                label={t("Insert a file")}
                isActive={menuPosition !== null}
                onClick={(event) =>
                    menuPosition ? closeMenu() : openMenu(event)
                }
            />
            {onAttachFiles && (
                <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    hidden
                    onChange={(event) => {
                        const files = Array.from(event.target.files ?? []);
                        if (files.length > 0) void onAttachFiles(files);
                        // Reset so picking the same file again re-triggers
                        // change.
                        event.target.value = "";
                    }}
                />
            )}
            {menuPosition &&
                createPortal(
                    <div
                        className="mobile-toolbar__popover-layer"
                        ref={keepEditorFocusOnTap}
                    >
                        <div
                            className="mobile-toolbar__popover-backdrop"
                            onClick={closeMenu}
                        />
                        <div
                            className="mobile-toolbar__popover"
                            role="menu"
                            aria-label={t("Insert a file")}
                            style={{
                                left: menuPosition.left,
                                bottom: menuPosition.bottom,
                            }}
                        >
                            {canInsertImage && (
                                <button
                                    type="button"
                                    role="menuitem"
                                    className="mobile-toolbar__popover-item"
                                    onClick={() => {
                                        closeMenu();
                                        insertImageBlock(editor);
                                    }}
                                >
                                    <Icon icon={Picture} size={IconSize.MEDIUM} />
                                    {t("Insert image")}
                                </button>
                            )}
                            {onAttachFiles && (
                                <button
                                    type="button"
                                    role="menuitem"
                                    className="mobile-toolbar__popover-item"
                                    onClick={() => {
                                        closeMenu();
                                        fileInputRef.current?.click();
                                    }}
                                >
                                    <Icon
                                        icon={AttachFile}
                                        size={IconSize.MEDIUM}
                                    />
                                    {t("Attach a file")}
                                </button>
                            )}
                            {canPickFromDrive && (
                                <button
                                    type="button"
                                    role="menuitem"
                                    className="mobile-toolbar__popover-item"
                                    onClick={() => {
                                        closeMenu();
                                        void drivePicker.pick().then((files) => {
                                            if (files.length > 0) {
                                                onDriveAttachmentPick!(files);
                                            }
                                        });
                                    }}
                                >
                                    <DriveIcon size="small" />
                                    {t("From {{driveAppName}}", {
                                        driveAppName: drivePicker.appName,
                                    })}
                                </button>
                            )}
                        </div>
                    </div>,
                    document.body,
                )}
        </>
    );
};
