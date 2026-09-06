import { createReactBlockSpec, useBlockNoteEditor, useComponentsContext, useEditorSelectionChange, useEditorChange, useEditorState } from "@blocknote/react";
import { IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useCallback, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { MobileToolbarButton } from "@/features/blocknote/mobile-toolbar/buttons";
import { useMobileToolbarDrawer } from "@/features/blocknote/mobile-toolbar/drawer-context";
import { Drawer } from "@/features/ui/components/drawer";
import { Props } from "@blocknote/core";
import DomPurify from "dompurify";
import { keepPreviousData } from "@tanstack/react-query";
import { ReadMessageTemplate, useMailboxesMessageTemplatesRenderRetrieve } from "@/features/api/gen";
import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema, PartialMessageComposerBlockSchema } from "@/features/forms/components/message-composer";
import { useTranslation } from "react-i18next";
import { MessageComposerHelper } from "@/features/utils/composer-helper";
import { useHtmlWithObjectUrls } from "@/features/blocknote/image-block/use-html-with-object-urls";
import { Icon } from "@/features/ui/components/icon";
import { Lock as LockIcon } from "@gouvfr-lasuite/ui-kit/icons";


/**
 * Converts layout tables (role="presentation") into flex divs so that
 * ProseMirror does not try to parse them as BlockNote table blocks.
 * Only affects the editor preview — email export keeps real tables.
 */
function replaceLayoutTablesWithDivs(html: string): string {
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const layoutTables = doc.querySelectorAll('table[role="presentation"]');

    for (const table of layoutTables) {
        const wrapper = doc.createElement('div');
        wrapper.style.cssText = 'display:flex;width:100%';

        // Copy any extra inline styles from the table
        if (table instanceof HTMLElement && table.style.length > 0) {
            for (const prop of ['gap', 'margin', 'padding'] as const) {
                const val = table.style.getPropertyValue(prop);
                if (val) wrapper.style.setProperty(prop, val);
            }
        }

        const cells = table.querySelectorAll('td');
        for (const td of cells) {
            const col = doc.createElement('div');
            // Carry over the td's inline styles (width, vertical-align, padding…)
            col.style.cssText = td.style.cssText;
            col.innerHTML = td.innerHTML;
            wrapper.appendChild(col);
        }

        table.replaceWith(wrapper);
    }

    return doc.body.innerHTML;
}

const SIGNATURES_DRAWER_ID = "signature-templates";

type SignatureTemplateSelectorProps = {
    mailboxId?: string;
    messageId?: string;
    templates?: ReadMessageTemplate[];
    defaultSelected?: string | null;
    isLoading?: boolean;
}

/**
 * A BlockNote toolbar selector which allows the user to select a signature template from
 * all active signatures for a given mailbox.
 */
export const SignatureTemplateSelector = ({ mailboxId, messageId, templates = [], defaultSelected, isLoading }: SignatureTemplateSelectorProps) => {
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const { t } = useTranslation();
    const Components = useComponentsContext()!;
    // Non-null when rendered inside the mobile toolbar: signatures are then
    // picked from a bottom drawer instead of the desktop inline select.
    const mobileDrawer = useMobileToolbarDrawer();

    const hasInlineContent = useEditorState({
        editor,
        selector: ({ editor }) => {
            const selectedBlocks = editor.getSelection()?.blocks || [
                editor.getTextCursorPosition().block,
            ];
            return selectedBlocks.some((block) => block.content !== undefined);
        },
    });

    const [isSelected, setIsSelected] = useState<string | null>(defaultSelected ?? null);
    const forcedTemplate = templates.find(template => template.is_forced);
    const isForced = !!forcedTemplate;

    const handleEditorContentOrSelectionChange = useCallback(() => {
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock) {
            setIsSelected((signatureBlock.props as BlockSignatureConfigProps).templateId);
        } else {
            setIsSelected(null);
        }
    }, [editor]);

    // Updates state on content or selection change.
    useEditorSelectionChange(handleEditorContentOrSelectionChange, editor);
    useEditorChange(handleEditorContentOrSelectionChange, editor);

    if (!hasInlineContent) return null;

    if (isLoading) {
        if (mobileDrawer) {
            return (
                <MobileToolbarButton
                    icon={<Spinner size="sm" />}
                    label={t("Loading templates...")}
                    isDisabled
                    onClick={() => {}}
                />
            );
        }
        return (
            <Components.FormattingToolbar.Button
                icon={<Spinner size="sm" />}
                isDisabled={true}
                label={t("Loading templates...")}
                mainTooltip={t("Loading templates...")}
            />
        );
    }

    if (templates.length === 0) {
        return null;
    }

    if (isForced) {
        if (mobileDrawer) {
            return (
                <MobileToolbarButton
                    icon={<Icon icon={LockIcon} size={IconSize.MEDIUM} />}
                    label={t("This signature is forced")}
                    isDisabled
                    onClick={() => {}}
                />
            );
        }
        return <Components.FormattingToolbar.Button
                className="signature-block-selector signature-block-selector--forced"
                icon={<Icon name="lock" size={IconSize.SMALL} />}
                mainTooltip={t("This signature is forced")}
                secondaryTooltip={t("You cannot modify it.")}
            >
                <div className="signature-block-selector__content">
                    <Icon icon={LockIcon} size={IconSize.SMALL} />
                    <p>{forcedTemplate.name}</p>
                </div>
            </Components.FormattingToolbar.Button>;
    }

    // Shared between the desktop inline select and the mobile drawer list.
    const signatureItems = [
        {
            text: t("No signatures"),
            isSelected: !isSelected,
            isDisabled: false,
            icon: <Icon name="signature" size={IconSize.SMALL} />,
            onClick: () => {
                editor.removeBlocks(["signature"]);
            },
        },
        ...templates.map((template) => ({
            text: template.name,
            isSelected: isSelected === template.id,
            isDisabled: template.is_forced,
            icon: template.is_forced
                ? <Icon icon={LockIcon} size={IconSize.SMALL} />
                : <Icon name="sign" size={IconSize.SMALL} />,
            onClick: () => {
                const signatureBlock = editor.getBlock('signature');

                // If this signature is already selected, check if it can be deselected
                if (isSelected === template.id) {
                    // If signature is forced, prevent deselection
                    if (template.is_forced) {
                        return; // Do nothing - forced signatures cannot be deselected
                    }

                    // Otherwise, remove it (toggle off)
                    if (signatureBlock) {
                        editor.removeBlocks(["signature"]);
                    }
                    return;
                }

                // Insert the signature without forcing a draft to exist: the
                // server resolves placeholders from the mailbox/user context,
                // and the messageId is patched once a draft is created from
                // real user content.
                const newBlock = {
                    id: "signature",
                    type: "signature" as const,
                    props: {
                        templateId: template.id,
                        mailboxId: mailboxId,
                        messageId: messageId,
                    }
                };

                if (signatureBlock) {
                    // Replace existing signature
                    editor.replaceBlocks(
                        ["signature"],
                        [newBlock] as unknown as PartialMessageComposerBlockSchema[]
                    );
                } else {
                    // Put signature at the end of the document or before the quote block if it exists
                    MessageComposerHelper.insertSignatureBlock(editor, newBlock);
                }
            }
        })),
    ];

    if (mobileDrawer) {
        return (
            <>
                <MobileToolbarButton
                    icon={<Icon name="signature" size={IconSize.MEDIUM} />}
                    label={t("Signature")}
                    isActive={!!isSelected || mobileDrawer.openId === SIGNATURES_DRAWER_ID}
                    onClick={() => mobileDrawer.open(SIGNATURES_DRAWER_ID)}
                />
                {mobileDrawer.openId === SIGNATURES_DRAWER_ID &&
                    mobileDrawer.slot &&
                    createPortal(
                        <Drawer title={t("Signature")} onClose={mobileDrawer.close}>
                            <div className="drawer-list">
                                {signatureItems.map((item) => (
                                    <button
                                        type="button"
                                        key={item.text}
                                        className={`drawer-list__item${item.isSelected ? " drawer-list__item--selected" : ""}`}
                                        disabled={item.isDisabled}
                                        aria-pressed={item.isSelected}
                                        onClick={() => {
                                            item.onClick();
                                            mobileDrawer.close();
                                        }}
                                    >
                                        {item.icon}
                                        <span className="drawer-list__item-label">
                                            {item.text}
                                        </span>
                                        {item.isSelected && (
                                            <Icon name="check" size={IconSize.SMALL} />
                                        )}
                                    </button>
                                ))}
                            </div>
                        </Drawer>,
                        mobileDrawer.slot,
                    )}
            </>
        );
    }

    return (
      <Components.FormattingToolbar.Select
        key="signatureTemplateSelector"
        items={signatureItems}
      />
    );
  }

/**
 * A BlockNote custom block which displays a signature template.
 */
export const BlockSignature = createReactBlockSpec(
    {
        type: "signature",
        content: "none",
        propSchema: {
            templateId: { default: "" },
            mailboxId: { default: "" },
            messageId: { default: "" },
        }
    },
    {
        render: ({ block : { props }}) => {
            const enabled = !!props.mailboxId && !!props.templateId;

            // The server renders the template with placeholders already
            // resolved from the mailbox/user context. A draft (messageId) is
            // only needed for message-level placeholders (recipient_name), so
            // the signature renders correctly even before a draft exists.
            // keepPreviousData avoids a spinner flash when messageId appears
            // (draft creation) and the query re-runs with recipient_name.
            // eslint-disable-next-line react-hooks/rules-of-hooks
            const { data: { data: rendered = null } = {}, isLoading } = useMailboxesMessageTemplatesRenderRetrieve(
                props.mailboxId,
                props.templateId,
                props.messageId ? { message_id: props.messageId } : undefined,
                { query: { enabled, placeholderData: keepPreviousData } },
            );

            // eslint-disable-next-line react-hooks/rules-of-hooks
            const sanitizedHtml = useMemo(() => {
                if (!rendered?.html_body) return null;
                const domPurify = DomPurify();
                const sanitized = domPurify.sanitize(rendered.html_body);
                // Replace layout tables with flex divs to prevent BlockNote from
                // parsing them as table blocks (which causes a crash).
                const transformed = replaceLayoutTablesWithDivs(sanitized);
                // Re-sanitize after the rewrite: replaceLayoutTablesWithDivs
                // reparses and re-serializes via innerHTML, and the result is
                // injected into the app origin with dangerouslySetInnerHTML (no
                // iframe boundary). A second pass guarantees the DOM rewrite
                // can't reintroduce anything unsafe. cid: image refs survive
                // here; the cid->blob: object-URL swap runs afterwards on
                // already-clean HTML.
                return domPurify.sanitize(transformed);
            }, [rendered?.html_body]);

            // eslint-disable-next-line react-hooks/rules-of-hooks
            const html = useHtmlWithObjectUrls(sanitizedHtml);

            if (isLoading) {
                return <Spinner size="sm" />;
            }

            if (!html) {
                return null;
            }

            return (
                <div style={{ userSelect: 'none', width: '100%' }} dangerouslySetInnerHTML={{ __html: html }} />
            )
        },
        toExternalHTML: () => (<span />),
        meta: {
            selectable: false,
        }
    }
)

export type BlockSignatureConfigProps = Props<ReturnType<typeof BlockSignature>["config"]["propSchema"]>;
