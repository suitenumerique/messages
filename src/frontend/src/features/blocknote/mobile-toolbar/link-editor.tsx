import { useBlockNoteEditor } from "@blocknote/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RiArrowLeftSLine, RiCheckLine, RiLinkUnlinkM } from "react-icons/ri";

import { MobileToolbarButton } from "./buttons";

const hasScheme = (url: string) => /^[a-z][a-z\d+.-]*:/i.test(url);

/**
 * Replaces the toolbar row with a URL input to create, edit or remove a link
 * on the current selection.
 */
export const MobileLinkEditor = ({ onClose }: { onClose: () => void }) => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor();
    // Snapshot on mount: the linked URL if the cursor sits inside a link.
    const [initialUrl] = useState(() => editor.getSelectedLinkUrl() ?? "");
    const [url, setUrl] = useState(initialUrl);

    const closeAndRefocus = () => {
        editor.focus();
        onClose();
    };

    const remove = () => {
        // deleteLink resolves the full link range from the cursor by itself.
        if (initialUrl) editor.deleteLink();
        closeAndRefocus();
    };

    const apply = () => {
        const trimmed = url.trim();
        if (!trimmed) {
            remove();
            return;
        }
        const href = hasScheme(trimmed) ? trimmed : `https://${trimmed}`;
        // createLink only marks the selected range: when editing an existing
        // link from a collapsed cursor, extend the selection to the whole mark
        // first, otherwise the new URL would be applied to nothing.
        if (initialUrl) editor._tiptapEditor.commands.extendMarkRange("link");
        const selectedText = editor.getSelectedText();
        editor.createLink(href, selectedText ? undefined : href);
        closeAndRefocus();
    };

    return (
        <div className="mobile-toolbar__link-editor">
            <MobileToolbarButton
                icon={<RiArrowLeftSLine size={24} />}
                label={t("Cancel")}
                onClick={closeAndRefocus}
            />
            <input
                className="mobile-toolbar__link-input"
                type="url"
                autoFocus
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key === "Enter") {
                        event.preventDefault();
                        apply();
                    }
                    if (event.key === "Escape") closeAndRefocus();
                }}
                placeholder={t("Link URL")}
                enterKeyHint="done"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
            />
            {initialUrl && (
                <MobileToolbarButton
                    icon={<RiLinkUnlinkM size={20} />}
                    label={t("Remove link")}
                    onClick={remove}
                />
            )}
            <MobileToolbarButton
                icon={<RiCheckLine size={24} />}
                label={t("Apply")}
                isDisabled={!url.trim() && !initialUrl}
                onClick={apply}
            />
        </div>
    );
};
