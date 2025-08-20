import { createReactBlockSpec, useBlockNoteEditor, useComponentsContext, useEditorContentOrSelectionChange } from "@blocknote/react";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useEffect, useState } from "react";
import { Props } from "@blocknote/core";
import { ReadOnlyMessageTemplate, useMessageTemplatesRenderRetrieve } from "@/features/api/gen";
import { MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema, PartialMessageComposerBlockSchema } from "@/features/forms/components/message-composer";


type SignatureTemplateSelectorProps = {
    mailboxId?: string;
    templates?: ReadOnlyMessageTemplate[];
    isLoading?: boolean;
}

/**
 * A BlockNote toolbar selector which allows the user to select a signature template from
 * all active signatures for a given mailbox.
 */
export const SignatureTemplateSelector = ({ mailboxId, templates = [], isLoading }: SignatureTemplateSelectorProps) => {
    const editor = useBlockNoteEditor<MessageComposerBlockSchema, MessageComposerInlineContentSchema, MessageComposerStyleSchema>();
    const Components = useComponentsContext()!;

    // Tracks whether the text & background are both blue.
    const [isSelected, setIsSelected] = useState<string>();

    // Updates state on content or selection change.
    useEditorContentOrSelectionChange(() => {
        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock) {
            setIsSelected((signatureBlock.props as BlockSignatureConfigProps).templateId);
        }
    }, editor);

    useEffect(() => {
        if(!isSelected) {
            const signatureBlock = editor.getBlock('signature');
            if (signatureBlock) {
                setIsSelected((signatureBlock.props as BlockSignatureConfigProps).templateId);
            }
        }
    }, []);

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
            text: `Signature : ${template.name}`,
            isSelected: isSelected === template.id,
            icon: <Icon name="content_copy" size={IconSize.SMALL} />,
            onClick: () => {
                const signatureBlock = editor.getBlock('signature');
                if (signatureBlock) {
                    editor.replaceBlocks(
                        ["signature"],
                        [{ id: "signature", type: "signature", props: { templateId: template.id, mailboxId: mailboxId } }] as unknown as PartialMessageComposerBlockSchema[]
                    );
                } else {
                    editor.insertBlocks(
                        [{ id: "signature", type: "signature", props: { templateId: template.id, mailboxId: mailboxId } }] as unknown as PartialMessageComposerBlockSchema[],
                        editor.document[0].id,
                        "after"
                    );
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
        toExternalHTML: ({ block : { props }}) => {
            // This will be parsed by the backend to insert the signature in the message body
            return <p>{`<SignatureID>${props.templateId}</SignatureID>`}</p>
        }
    }
)
export type BlockSignatureConfigProps = Props<typeof BlockSignature['config']["propSchema"]>;
