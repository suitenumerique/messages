import { Key, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import {
    Autocomplete,
    Header,
    Input,
    Menu,
    MenuItem,
    MenuSection,
    SearchField,
    useFilter,
} from "react-aria-components";
import { IconSize, IconType } from "@gouvfr-lasuite/ui-components";
import { ReadMessageTemplate } from "@/features/api/gen";
import { Icon } from "@/features/ui/components/icon";
import { StringHelper } from "@/features/utils/string-helper";
import { MessageTemplatePreview } from "./message-template-preview";
import { useRecentMessageTemplates } from "./use-recent-message-templates";

// Arrow-key browsing would otherwise render every template it goes through.
const PREVIEW_DELAY_MS = 150;

type MessageTemplatePickerProps = {
    mailboxId: string;
    messageId?: string;
    templates: ReadMessageTemplate[];
    onSelect: (templateId: string) => void;
    showPreview?: boolean;
    autoFocus?: boolean;
};

/**
 * Keyboard-first message template picker: a search field filtering the
 * templates (accent and case insensitive), the recently used ones listed
 * first, and an optional preview of the focused template.
 *
 * `Autocomplete` keeps the DOM focus in the search field and moves a virtual
 * focus across the menu items, so users can type, browse with the arrow keys
 * and insert with Enter without leaving the field.
 */
export const MessageTemplatePicker = ({
    mailboxId,
    messageId,
    templates,
    onSelect,
    showPreview = false,
    autoFocus = true,
}: MessageTemplatePickerProps) => {
    const { t } = useTranslation();
    const { recentIds, addRecent } = useRecentMessageTemplates(mailboxId);

    // Recents that no longer exist (deleted, deactivated) are skipped. The
    // other templates are sorted by name: with dozens of them, an order users
    // can predict beats the creation date.
    const { recentTemplates, otherTemplates } = useMemo(() => {
        const templatesById = new Map(templates.map((template) => [template.id, template]));
        const recent = recentIds
            .map((id) => templatesById.get(id))
            .filter((template): template is ReadMessageTemplate => !!template);
        const recentSet = new Set(recent.map((template) => template.id));
        const others = templates
            .filter((template) => !recentSet.has(template.id))
            .sort((a, b) => a.name.localeCompare(b.name));
        return { recentTemplates: recent, otherTemplates: others };
    }, [templates, recentIds]);

    const [focusedId, setFocusedId] = useState<string | null>(null);
    const [previewId, setPreviewId] = useState<string | null>(null);
    useEffect(() => {
        if (!showPreview) return;
        const timeout = setTimeout(() => setPreviewId(focusedId), PREVIEW_DELAY_MS);
        return () => clearTimeout(timeout);
    }, [focusedId, showPreview]);
    const previewTemplate = previewId
        ? templates.find((template) => template.id === previewId)
        : undefined;
    const reportFocus = useCallback((templateId: string, isFocused: boolean) => {
        setFocusedId((current) => {
            if (isFocused) return templateId;
            return current === templateId ? null : current;
        });
    }, []);

    // Focused from an effect rather than with the autoFocus attribute: the
    // modal (react-modal) focuses its own content once its children are
    // mounted, which would steal the focus back from the field.
    const searchInputRef = useRef<HTMLInputElement>(null);
    useEffect(() => {
        if (autoFocus) searchInputRef.current?.focus();
    }, [autoFocus]);

    const { contains } = useFilter({ sensitivity: "base" });
    const matchTemplate = (textValue: string, inputValue: string) => {
        if (!inputValue) return true;
        return contains(
            StringHelper.normalizeForSearch(textValue),
            StringHelper.normalizeForSearch(inputValue),
        );
    };

    const handleAction = (key: Key) => {
        const templateId = String(key);
        addRecent(templateId);
        onSelect(templateId);
    };

    const renderItem = (template: ReadMessageTemplate) => (
        <MenuItem
            key={template.id}
            id={template.id}
            textValue={template.name}
            className="message-template-picker__item"
        >
            {({ isFocused }) => (
                <>
                    <FocusReporter templateId={template.id} isFocused={isFocused} onFocusChange={reportFocus} />
                    <Icon
                        name="description"
                        type={IconType.OUTLINED}
                        size={IconSize.MEDIUM}
                        className="message-template-picker__item-icon"
                    />
                    <span className="message-template-picker__item-label">{template.name}</span>
                </>
            )}
        </MenuItem>
    );

    return (
        <div className={clsx("message-template-picker", { "message-template-picker--with-preview": showPreview })}>
            <div className="message-template-picker__browser">
                <Autocomplete filter={matchTemplate}>
                    <SearchField
                        aria-label={t("Search a template")}
                        className="message-template-picker__search"
                    >
                        <Icon
                            name="search"
                            type={IconType.OUTLINED}
                            size={IconSize.SMALL}
                            className="message-template-picker__search-icon"
                        />
                        <Input
                            ref={searchInputRef}
                            placeholder={t("Search a template")}
                            className="message-template-picker__search-input"
                        />
                    </SearchField>
                    <Menu
                        aria-label={t("Message templates")}
                        onAction={handleAction}
                        className="message-template-picker__list"
                        renderEmptyState={() => (
                            <div className="message-template-picker__status">
                                {t("No matching templates")}
                            </div>
                        )}
                    >
                        {recentTemplates.length > 0 && (
                            <MenuSection className="message-template-picker__section">
                                <Header className="message-template-picker__section-header">
                                    {t("Recently used")}
                                </Header>
                                {recentTemplates.map(renderItem)}
                            </MenuSection>
                        )}
                        {otherTemplates.length > 0 && (
                            <MenuSection className="message-template-picker__section">
                                {recentTemplates.length > 0 && (
                                    <Header className="message-template-picker__section-header">
                                        {t("Other templates")}
                                    </Header>
                                )}
                                {otherTemplates.map(renderItem)}
                            </MenuSection>
                        )}
                    </Menu>
                </Autocomplete>
            </div>
            {showPreview && (
                <section
                    className="message-template-picker__preview"
                    aria-label={t("Template preview")}
                >
                    <p className="message-template-picker__preview-title">{t("Preview")}</p>
                    {previewTemplate ? (
                        <MessageTemplatePreview
                            mailboxId={mailboxId}
                            templateId={previewTemplate.id}
                            signatureId={previewTemplate.signature}
                            messageId={messageId}
                        />
                    ) : (
                        <div className="message-template-preview message-template-preview--status">
                            {t("Hover a template to preview it")}
                        </div>
                    )}
                </section>
            )}
        </div>
    );
};

type FocusReporterProps = {
    templateId: string;
    isFocused: boolean;
    onFocusChange: (templateId: string, isFocused: boolean) => void;
};

/**
 * Bridges react-aria's `isFocused` render prop, which follows both the
 * virtual keyboard focus and the mouse hover, to a callback. The focus loss
 * is also reported when the item unmounts, e.g. filtered out by the search.
 */
const FocusReporter = ({ templateId, isFocused, onFocusChange }: FocusReporterProps) => {
    useEffect(() => {
        if (!isFocused) return;
        onFocusChange(templateId, true);
        return () => onFocusChange(templateId, false);
    }, [templateId, isFocused, onFocusChange]);
    return null;
};
