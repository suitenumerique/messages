import { createReactBlockSpec, useBlockNoteEditor, useComponentsContext, useEditorContentOrSelectionChange } from "@blocknote/react";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";
import { Props } from "@blocknote/core";
import { ReadOnlyMessageTemplate, useMailboxesMessageTemplatesRenderRetrieve } from "@/features/api/gen";
import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema, PartialMessageComposerBlockSchema } from "@/features/forms/components/message-composer";
import { useTranslation } from "react-i18next";
import { MessageComposerHelper } from "@/features/utils/composer-helper";


type SignatureTemplateSelectorProps = {
    mailboxId?: string;
    templates?: ReadOnlyMessageTemplate[];
    defaultSelected?: string | null;
    isLoading?: boolean;
}

/**
 * A BlockNote toolbar selector which allows the user to select a signature template from
 * all active signatures for a given mailbox.
 */
export const SignatureTemplateSelector = ({ mailboxId, templates = [], defaultSelected, isLoading }: SignatureTemplateSelectorProps) => {
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const { t } = useTranslation();
    const Components = useComponentsContext()!;

    // Tracks whether the text & background are both blue.
    const [isSelected, setIsSelected] = useState<string | null>(defaultSelected ?? null);
    const forcedTemplate = templates.find(template => template.is_forced);
    const isForced = !!forcedTemplate;

    // Updates state on content or selection change.
    useEditorContentOrSelectionChange(() => {
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock) {
            setIsSelected((signatureBlock.props as BlockSignatureConfigProps).templateId);
        } else {
            setIsSelected(null);
        }
    }, editor);

    if (isLoading) {
        return <Spinner size="sm" />;
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
                    <p>{t('Signature: {{name}}', { name:  forcedTemplate.name })}</p>
                </div>
            </Components.FormattingToolbar.Button>;
    }

    return (
      <Components.FormattingToolbar.Select
        key={"templateVariableSelector"}
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
            text: t('Signature: {{name}}', { name:  template.name }),
            isSelected: isSelected === template.id,
            isDisabled: template.is_forced,
            icon: <Icon name={template.is_forced ? "lock" : "drive_file_rename_outline"} size={IconSize.SMALL} />,
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

                // Otherwise, add or replace the signature
                const newBlock = {
                    id: "signature",
                    type: "signature" as const,
                    props: {
                        templateId: template.id,
                        mailboxId: mailboxId
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
        isSelectable: false,
        isFileBlock: false,
        propSchema: {
            templateId: { default: "" },
            mailboxId: { default: "" },
            username: { default: "" },
        }
    },
    {
        render: ({ block : { props }}) => {
            // eslint-disable-next-line react-hooks/rules-of-hooks
            const { data: { data: preview = null } = {}, isLoading } = useMailboxesMessageTemplatesRenderRetrieve(
                props.mailboxId,
                props.templateId,
                {
                    query: {
                        enabled: !!props.mailboxId && !!props.templateId,
                    }
                }
            );

            if (isLoading) {
                return <Spinner size="sm" />;
            }

            if (!preview?.html_body) {
                return null;
            }

            return (
                <div dangerouslySetInnerHTML={{ __html: preview.html_body }} />
            )
        },
        toExternalHTML: () => (<span />)
    }
)

export type BlockSignatureConfigProps = Props<typeof BlockSignature['config']["propSchema"]>;
