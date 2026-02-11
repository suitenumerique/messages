import { BlockNoteViewField } from "@/features/blocknote/blocknote-view-field";
import { BlockNoteEditorOptions, BlockNoteSchema, defaultBlockSpecs, defaultInlineContentSpecs } from "@blocknote/core";
import { InlineTemplateVariable, TemplateVariableSelector } from "@/features/blocknote/inline-template-variable";
import { FieldProps } from "@gouvfr-lasuite/cunningham-react";
import { useEffect } from "react";
import { Toolbar } from "@/features/blocknote/toolbar";
import { BlockSignature, BlockSignatureConfigProps, SignatureTemplateSelector } from "@/features/blocknote/signature-block";
import { MessageTemplateTypeChoices, useMailboxesMessageTemplatesAvailableList, usePlaceholdersRetrieve } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { imageBlockSpec } from "@/features/blocknote/image-block";
import { ImageUploadButton } from "@/features/blocknote/image-upload-button";
import { SmartTrailingBlock } from "@/features/blocknote/smart-trailing-block";
import { useBase64Composer } from "@/features/blocknote/hooks/use-base64-composer";
import { BodyHiddenInputs } from "@/features/blocknote/body-hidden-inputs";

const TEMPLATE_BLOCKNOTE_SCHEMA = BlockNoteSchema.create({
    blockSpecs: {
        ...defaultBlockSpecs,
        'image': imageBlockSpec,
        'signature': BlockSignature(),
    },
    inlineContentSpecs: {
        ...defaultInlineContentSpecs,
        'template-variable': InlineTemplateVariable,
    }
});

export type TemplateComposerBlockNoteSchema = typeof TEMPLATE_BLOCKNOTE_SCHEMA;
export type TemplateComposerBlockSchema = TemplateComposerBlockNoteSchema['blockSchema'];
export type TemplateComposerInlineContentSchema = TemplateComposerBlockNoteSchema['inlineContentSchema'];
export type TemplateComposerStyleSchema = TemplateComposerBlockNoteSchema['styleSchema'];

type TemplateComposerProps = FieldProps & {
    blockNoteOptions?: Partial<BlockNoteEditorOptions<TemplateComposerBlockSchema, TemplateComposerInlineContentSchema, TemplateComposerStyleSchema>>,
    defaultValue?: string | null;
    disabled?: boolean;
}

/**
 * The composer component for the template content.
 */
export const TemplateComposer = ({ blockNoteOptions, defaultValue, disabled = false, ...props }: TemplateComposerProps) => {
    const { selectedMailbox } = useMailboxContext();

    const { editor, handleChange } = useBase64Composer({
        schema: TEMPLATE_BLOCKNOTE_SCHEMA,
        defaultValue,
        blockNoteOptions,
        trailingBlock: false,
        extensions: [SmartTrailingBlock],
    });

    const { data: { data: placeholders = {} } = {}, isLoading: isLoadingPlaceholders } = usePlaceholdersRetrieve({
        query: {
            refetchOnMount: true,
            refetchOnWindowFocus: true,
        }
    });

    const { data: { data: activeSignatures = [] } = {}, isLoading: isLoadingSignatures } = useMailboxesMessageTemplatesAvailableList(
        selectedMailbox?.id || "",
        {
            type: MessageTemplateTypeChoices.signature,
        },
        {
            query: {
                enabled: !!selectedMailbox?.id,
                refetchOnMount: true,
                refetchOnWindowFocus: true,
            },
        }
    );

    // Detect current signature on mount and update it, then sync form values
    useEffect(() => {
        if(!editor) return;

        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock?.type === 'signature') {
            const templateId = signatureBlock.props.templateId;
            const signature = activeSignatures.find(s => s.id === templateId);
            if (signature) {
                editor.updateBlock(signatureBlock.id, {
                    type: 'signature',
                    props: {
                        templateId: signature.id,
                        mailboxId: selectedMailbox?.id,
                    }
                });
            }
        }
    }, [editor, activeSignatures, selectedMailbox?.id]);

    // Insert or remove forced signature block
    useEffect(() => {
        if (!editor || isLoadingSignatures) return;

        const signatureBlock = editor.getBlock('signature');
        if (signatureBlock) {
            const blockSignatureId = (signatureBlock.props as BlockSignatureConfigProps).templateId;
            const isSignatureStale = activeSignatures.findIndex(signature => signature.id === blockSignatureId) < 0;
            if (isSignatureStale) editor.removeBlocks(["signature"]);
            else return;
        }

        if (activeSignatures.length === 0) return;

        const signatureToUse = activeSignatures.find(signature => signature.is_forced);
        if (signatureToUse) {
            const newSignatureBlock = {
                id: "signature",
                type: "signature" as const,
                props: {
                    templateId: signatureToUse.id,
                    mailboxId: selectedMailbox?.id,
                }
            };

            if (editor.document.length === 0) {
                editor.insertBlocks([{ type: "paragraph", content: [{ type: "text", text: "", styles: {} }] }], "", "after");
            }

            editor.insertBlocks([newSignatureBlock], editor.document[editor.document.length - 1].id, "after");
        }
    }, [editor, isLoadingSignatures, activeSignatures, selectedMailbox?.id]);

    return (
        <>
            <BlockNoteViewField
                {...props}
                className="template-composer"
                fullWidth
                disabled={disabled}
                composerProps={{
                    editor,
                    onChange: handleChange,
                }}
            >
                <Toolbar>
                    <ImageUploadButton />
                    <SignatureTemplateSelector
                        templates={activeSignatures}
                        isLoading={isLoadingSignatures}
                        mailboxId={selectedMailbox?.id}
                    />
                    <TemplateVariableSelector
                        variables={placeholders}
                        isLoading={isLoadingPlaceholders}
                    />
                </Toolbar>
            </BlockNoteViewField>
            <BodyHiddenInputs />
        </>
    );
};
