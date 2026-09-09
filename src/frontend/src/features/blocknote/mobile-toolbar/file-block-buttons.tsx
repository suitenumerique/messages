import { blockHasType } from "@blocknote/core";
import { useBlockNoteEditor, useEditorState } from "@blocknote/react";
import { useRef, useState } from "react";
import { RiImageEditFill } from "react-icons/ri";

import { ALLOWED_IMAGE_MIME_TYPES } from "../image-block";
import { MobileToolbarButton } from "./buttons";
import { Icon } from "@/features/ui/components/icon";
import { Trash } from "@gouvfr-lasuite/ui-kit/icons";

/**
 * The single selected file-carrying block, or undefined. Mirrors the
 * selector BlockNote's own File* toolbar buttons render on (exactly one
 * selected block whose props carry a url), so the mobile row swaps to the
 * file actions at the same moment those would appear.
 */
export const useSelectedFileBlock = () => {
    const editor = useBlockNoteEditor();
    return useEditorState({
        editor,
        selector: ({ editor }) => {
            if (!editor.isEditable) return undefined;
            const selectedBlocks = editor.getSelection()?.blocks || [
                editor.getTextCursorPosition().block,
            ];
            if (selectedBlocks.length !== 1) return undefined;
            const block = selectedBlocks[0];
            return blockHasType(block, editor, block.type, { url: "string" })
                ? block
                : undefined;
        },
    });
};

/**
 * Replaces the selected file block's content straight from the OS file
 * picker. Not BlockNote's FileReplaceButton: its mantine popover renders
 * inside the toolbar, whose focus trap (useFocusTrap in the mantine Toolbar
 * component) drops the focus as soon as the panel's URL input takes it —
 * folding the whole bar. A direct picker also skips the Upload/Embed tabs,
 * which is the right amount of UI on a phone.
 */
export const FileReplaceMobileButton = () => {
    const editor = useBlockNoteEditor();
    const block = useSelectedFileBlock();
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [isUploading, setIsUploading] = useState(false);

    // No uploadFile configured (signature / template composers): no way to
    // turn a picked file into a URL, so the action is not offered.
    if (!block || !editor.uploadFile) return null;

    const label =
        editor.dictionary.formatting_toolbar.file_replace.tooltip[block.type] ||
        editor.dictionary.formatting_toolbar.file_replace.tooltip.file;

    return (
        <>
            <MobileToolbarButton
                icon={<RiImageEditFill size={24} />}
                label={label}
                isDisabled={isUploading}
                onClick={() => fileInputRef.current?.click()}
            />
            <input
                ref={fileInputRef}
                type="file"
                accept={ALLOWED_IMAGE_MIME_TYPES.join(",")}
                hidden
                onChange={async (event) => {
                    const file = event.target.files?.[0];
                    // Reset so picking the same file again re-triggers change.
                    event.target.value = "";
                    if (!file) return;
                    setIsUploading(true);
                    try {
                        // No blockId passed on purpose: the composer's
                        // uploadFile removes that block when the upload fails,
                        // and a failed *replacement* must keep the current
                        // image. Failure yields an empty URL, skipped below.
                        const url = await editor.uploadFile!(file);
                        if (typeof url === "string" && url) {
                            editor.updateBlock(block, { props: { url } });
                        }
                    } finally {
                        setIsUploading(false);
                    }
                }}
            />
        </>
    );
};

/**
 * Deletes the selected file block. Replaces BlockNote's FileDeleteButton so
 * the mobile bar only contains its own buttons (shared styling, no mantine
 * chrome to neutralize).
 */
export const FileDeleteMobileButton = () => {
    const editor = useBlockNoteEditor();
    const block = useSelectedFileBlock();

    if (!block) return null;

    const label =
        editor.dictionary.formatting_toolbar.file_delete.tooltip[block.type] ||
        editor.dictionary.formatting_toolbar.file_delete.tooltip.file;

    return (
        <MobileToolbarButton
            icon={<Icon icon={Trash} />}
            label={label}
            onClick={() => {
                editor.focus();
                editor.removeBlocks([block.id]);
            }}
        />
    );
};
