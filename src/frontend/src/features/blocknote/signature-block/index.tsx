import { createReactBlockSpec, useBlockNoteEditor, useComponentsContext, useEditorSelectionChange, useEditorChange, useEditorState } from "@blocknote/react";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useMemo, useState } from "react";
import { Props } from "@blocknote/core";
import DomPurify from "dompurify";
import { ReadMessageTemplate, useMailboxesMessageTemplatesRetrieve, useDraftPlaceholdersRetrieve, DraftPlaceholdersRetrieve200 } from "@/features/api/gen";
import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema, PartialMessageComposerBlockSchema } from "@/features/forms/components/message-composer";
import { useTranslation } from "react-i18next";
import { MessageComposerHelper } from "@/features/utils/composer-helper";
import { useHtmlWithObjectUrls } from "@/features/blocknote/image-block/use-html-with-object-urls";


type SignatureTemplateSelectorProps = {
    mailboxId?: string;
    messageId?: string;
    ensureDraft?: () => Promise<string | undefined>;
    templates?: ReadMessageTemplate[];
    defaultSelected?: string | null;
    isLoading?: boolean;
}

/**
 * A BlockNote toolbar selector which allows the user to select a signature template from
 * all active signatures for a given mailbox.
 */
export const SignatureTemplateSelector = ({ mailboxId, messageId, ensureDraft, templates = [], defaultSelected, isLoading }: SignatureTemplateSelectorProps) => {
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const { t } = useTranslation();
    const Components = useComponentsContext()!;

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

    const handleEditorContentOrSelectionChange = () => {
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock) {
            setIsSelected((signatureBlock.props as BlockSignatureConfigProps).templateId);
        } else {
            setIsSelected(null);
        }
    }

    // Updates state on content or selection change.
    useEditorSelectionChange(handleEditorContentOrSelectionChange, editor);
    useEditorChange(handleEditorContentOrSelectionChange, editor);

    if (!hasInlineContent) return null;

    if (isLoading) {
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
        return <Components.FormattingToolbar.Button
                className="signature-block-selector signature-block-selector--forced"
                icon={<Icon name="lock" size={IconSize.SMALL} />}
                mainTooltip={t("This signature is forced")}
                secondaryTooltip={t("You cannot modify it.")}
            >
                <div className="signature-block-selector__content">
                    <Icon name="lock" size={IconSize.SMALL} />
                    <p>{forcedTemplate.name}</p>
                </div>
            </Components.FormattingToolbar.Button>;
    }

    return (
      <Components.FormattingToolbar.Select
        key="signatureTemplateSelector"
        items={[
          {
            text: t("No signature"),
            isSelected: !isSelected,
            isDisabled: false,
            icon: <Icon name="drive_file_rename_outline" size={IconSize.SMALL} />,
            onClick: () => {
                editor.removeBlocks(["signature"]);
            },
          },
          ...templates.map((template) => ({
            text: template.name,
            isSelected: isSelected === template.id,
            isDisabled: template.is_forced,
            icon: <Icon name={template.is_forced ? "lock" : "drive_file_rename_outline"} size={IconSize.SMALL} />,
            onClick: async () => {
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

                const resolvedMessageId = messageId ?? await ensureDraft?.();

                // Otherwise, add or replace the signature
                const newBlock = {
                    id: "signature",
                    type: "signature" as const,
                    props: {
                        templateId: template.id,
                        mailboxId: mailboxId,
                        messageId: resolvedMessageId,
                    }
                };

                if (signatureBlock) {
                    // Replace existing signature
                    editor.replaceBlocks(
                        ["signature"],
                        [newBlock] as unknown as PartialMessageComposerBlockSchema[]
                    );
                } else {
                    // Insert signature at the end of the document
                    if (editor.document.length === 0) {
                        // If document is empty, first add an empty paragraph
                        editor.insertBlocks(
                            [{ type: "paragraph", content: [{ type: "text", text: "", styles: {} }] }] as unknown as PartialMessageComposerBlockSchema[],
                            "",
                            "after"
                        );
                    }

                    // Put signature at the end of the document or before the quote block if it exists
                    MessageComposerHelper.insertSignatureBlock(editor, newBlock);
                }
            }
          })),
        ]}
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

            // eslint-disable-next-line react-hooks/rules-of-hooks
            const { data: { data: template = null } = {}, isFetching: isLoadingTemplate } = useMailboxesMessageTemplatesRetrieve(
                props.mailboxId,
                props.templateId,
                { bodies: "html" },
                { query: { enabled } },
            );

            // eslint-disable-next-line react-hooks/rules-of-hooks
            const { data: { data: placeholders = {} } = {}, isFetching: isLoadingPlaceholders } = useDraftPlaceholdersRetrieve(
                props.messageId,
                { query: { enabled: enabled && !!props.messageId } },
            );

            const isLoading = isLoadingTemplate || isLoadingPlaceholders;

            // eslint-disable-next-line react-hooks/rules-of-hooks
            const sanitizedHtml = useMemo(() => {
                if (isLoading || !template?.html_body) return null;
                let html = template.html_body;
                for (const [key, value] of Object.entries(placeholders as DraftPlaceholdersRetrieve200)) {
                    html = html.replaceAll(`{${key}}`, value);
                }
                return DomPurify().sanitize(html);
            }, [template?.html_body, placeholders, isLoading]);

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
