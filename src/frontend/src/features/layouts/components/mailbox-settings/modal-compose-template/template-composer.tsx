import { BlockNoteViewField } from "@/features/blocknote/blocknote-view-field";
import { BlockNoteEditor, BlockNoteEditorOptions, BlockNoteSchema, defaultBlockSpecs, defaultInlineContentSpecs } from "@blocknote/core";
import { InlineTemplateVariable, TemplateVariableSelector } from "@/features/blocknote/inline-template-variable";
import { FieldProps } from "@gouvfr-lasuite/cunningham-react";
import { forwardRef, useEffect, useImperativeHandle, useMemo } from "react";
import { useFormContext } from "react-hook-form";
import { Toolbar } from "@/features/blocknote/toolbar";
import { BlockSignature, BlockSignatureConfigProps, SignatureTemplateSelector } from "@/features/blocknote/signature-block";
import { MessageTemplateTypeChoices, useMailboxesMessageTemplatesAvailableList, usePlaceholdersRetrieve } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { imageBlockSpec } from "@/features/blocknote/image-block";
import { SmartTrailingBlock } from "@/features/blocknote/smart-trailing-block";
import { useBase64Composer, Base64ComposerHandle } from "@/features/blocknote/hooks/use-base64-composer";
import { extractSignatureId } from "../utils";
import { SuggestionMenuController } from "@blocknote/react";
import { filterSuggestionItems } from "@blocknote/core/extensions";

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
    allowVariables?: boolean;
}

/**
 * The composer component for the template content.
 */
export const TemplateComposer = forwardRef<Base64ComposerHandle, TemplateComposerProps>(({ blockNoteOptions, defaultValue, disabled = false, allowVariables = true, ...props }, ref) => {
    const form = useFormContext();
    const { selectedMailbox } = useMailboxContext();

    const { editor, handleChange, exportContent } = useBase64Composer({
        schema: TEMPLATE_BLOCKNOTE_SCHEMA,
        defaultValue,
        blockNoteOptions,
        trailingBlock: false,
        extensions: [SmartTrailingBlock],
    });

    useImperativeHandle(ref, () => ({ exportContent }), [exportContent]);

    const { data: { data: placeholders = {} } = {}, isLoading: isLoadingPlaceholders } = usePlaceholdersRetrieve({
        query: {
            enabled: allowVariables,
            refetchOnMount: true,
            refetchOnWindowFocus: true,
        }
    });
    const canShowPlaceholdersMenu = allowVariables && !isLoadingPlaceholders && !!Object.keys(placeholders).length;
    const getPlaceholderMenuItems = (editor: BlockNoteEditor<TemplateComposerBlockSchema, TemplateComposerInlineContentSchema, TemplateComposerStyleSchema>) => {
        return Object.entries(placeholders).map(([value, label]) => ({
            title: label,
            onItemClick: () => {
                editor.insertInlineContent([{ type: "template-variable", props: { value, label } }, " "]);
            }
        }));
    };

    const defaultSignatureId = useMemo(() => {
        if (!defaultValue) return null;
        return extractSignatureId(defaultValue);
    }, [defaultValue]);

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

    // Manage signature block: update existing, replace stale, or insert forced
    useEffect(() => {
        if (!editor || isLoadingSignatures) return;

        const signatureBlock = editor.getBlock('signature');
        const forcedSignature = activeSignatures.find(signature => signature.is_forced);

        if (signatureBlock) {
            const templateId = (signatureBlock.props as BlockSignatureConfigProps).templateId;

            // Forced signature must take precedence
            if (forcedSignature && forcedSignature.id !== templateId) {
                editor.replaceBlocks(["signature"], [{
                    id: "signature",
                    type: "signature" as const,
                    props: {
                        templateId: forcedSignature.id,
                        mailboxId: selectedMailbox?.id,
                    }
                }]);
                return;
            }

            // Current signature is still active — keep it
            if (activeSignatures.some(s => s.id === templateId)) {
                return;
            }

            // Signature is stale — remove it
            editor.removeBlocks(["signature"]);
            return;
        }

        // No signature in editor — insert forced one if available
        if (!forcedSignature) return;

        const newSignatureBlock = {
            id: "signature",
            type: "signature" as const,
            props: {
                templateId: forcedSignature.id,
                mailboxId: selectedMailbox?.id,
            }
        };

        editor.insertBlocks([newSignatureBlock], editor.document[editor.document.length - 1].id, "after");
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
                    <SignatureTemplateSelector
                        templates={activeSignatures}
                        isLoading={isLoadingSignatures}
                        mailboxId={selectedMailbox?.id}
                        defaultSelected={defaultSignatureId}
                    />
                    {canShowPlaceholdersMenu &&
                        <TemplateVariableSelector
                            variables={placeholders}
                            isLoading={isLoadingPlaceholders}
                        />
                    }
                </Toolbar>
                {canShowPlaceholdersMenu &&
                    <SuggestionMenuController
                        triggerCharacter="{"
                        getItems={async (query) => filterSuggestionItems(getPlaceholderMenuItems(editor), query)}
                    />
                }
            </BlockNoteViewField>
            <input {...form.register("rawBody")} type="hidden" />
        </>
    );
});
TemplateComposer.displayName = "TemplateComposer";
