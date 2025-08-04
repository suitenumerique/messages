import { createReactBlockSpec, useBlockNoteEditor, useComponentsContext, useEditorContentOrSelectionChange } from "@blocknote/react";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";
import { Props } from "@blocknote/core";
import { ReadOnlyMessageTemplate, useMessageTemplatesRenderRetrieve } from "@/features/api/gen";
import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema, PartialMessageComposerBlockSchema } from "@/features/forms/components/message-composer";


type SignatureTemplateSelectorProps = {
    mailboxId?: string;
    templates?: ReadOnlyMessageTemplate[];
    isLoading?: boolean;
    onSignatureChange?: (signatureId: string | null) => void;
}

/**
 * A BlockNote toolbar selector which allows the user to select a signature template from
 * all active signatures for a given mailbox.
 */
export const SignatureTemplateSelector = ({ mailboxId, templates = [], isLoading, onSignatureChange }: SignatureTemplateSelectorProps) => {
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const Components = useComponentsContext()!;

    // Tracks whether the text & background are both blue.
    const [isSelected, setIsSelected] = useState<string | null>(null);

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

    return (
      <Components.FormattingToolbar.Select
        key={"templateVariableSelector"}
        items={[
          {
            text: "Signatures",
            isSelected: !isSelected,
            isDisabled: true,
            icon: <Icon name="content_copy" size={IconSize.SMALL} />,
            onClick: () => {}
          },
          ...templates.map((template) => ({
            text: `Signature : ${template.name}${template.is_forced ? ' (obligatoire)' : ''}`,
            isSelected: isSelected === template.id,
            icon: <Icon name={template.is_forced ? "lock" : "content_copy"} size={IconSize.SMALL} />,
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
                    onSignatureChange?.(null);
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
                    // Then insert signature after the last block
                    editor.insertBlocks(
                        [newBlock] as unknown as PartialMessageComposerBlockSchema[],
                        editor.document[editor.document.length - 1].id,
                        "after"
                    );
                }
                onSignatureChange?.(template.id);
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
            const { data: { data: preview = null } = {}, isLoading } = useMessageTemplatesRenderRetrieve(props.templateId, {
                request: {
                    params: {
                        mailbox_id: props.mailboxId
                    }
                }
            });

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
