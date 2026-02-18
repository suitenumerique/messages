import { BlockNoteViewField } from "@/features/blocknote/blocknote-view-field";
import { BlockNoteEditor, BlockNoteEditorOptions, BlockNoteSchema, defaultBlockSpecs, defaultInlineContentSpecs, PartialBlock } from "@blocknote/core";
import { filterSuggestionItems } from "@blocknote/core/extensions";
import { SuggestionMenuController } from "@blocknote/react";
import { FieldProps } from "@gouvfr-lasuite/cunningham-react";
import { forwardRef, useImperativeHandle } from "react";
import { useFormContext } from "react-hook-form";
import { InlineTemplateVariable, TemplateVariableSelector } from "@/features/blocknote/inline-template-variable";
import { Toolbar } from "@/features/blocknote/toolbar";
import { usePlaceholdersRetrieve } from "@/features/api/gen";
import { imageBlockSpec } from "@/features/blocknote/image-block";
import { useBase64Composer, Base64ComposerHandle } from "@/features/blocknote/hooks/use-base64-composer";
import { ColumnBlock, ColumnListBlock } from "@/features/blocknote/column-layout-block";

const SIGNATURE_BLOCKNOTE_SCHEMA = BlockNoteSchema.create({
    blockSpecs: {
        ...defaultBlockSpecs,
        'image': imageBlockSpec,
        'column': ColumnBlock,
        'columnList': ColumnListBlock,
    },
    inlineContentSpecs: {
        ...defaultInlineContentSpecs,
        'template-variable': InlineTemplateVariable,
    }
});

export type SignatureComposerBlockNoteSchema = typeof SIGNATURE_BLOCKNOTE_SCHEMA;
export type SignatureComposerBlockSchema = SignatureComposerBlockNoteSchema['blockSchema'];
export type SignatureComposerInlineContentSchema = SignatureComposerBlockNoteSchema['inlineContentSchema'];
export type SignatureComposerStyleSchema = SignatureComposerBlockNoteSchema['styleSchema'];
export type PartialSignatureComposerBlockSchema = PartialBlock<SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema>;

type SignatureComposerProps = FieldProps & {
    blockNoteOptions?: Partial<BlockNoteEditorOptions<SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema>>,
    defaultValue?: string | null;
    disabled?: boolean;
}

/**
 * Shared composer component for signature content.
 * Used by both admin (maildomain) and mailbox signature modals.
 */
export const SignatureComposer = forwardRef<Base64ComposerHandle, SignatureComposerProps>(({ blockNoteOptions, defaultValue, disabled = false, ...props }, ref) => {
    const form = useFormContext();
    const { editor, handleChange, exportContent } = useBase64Composer({
        schema: SIGNATURE_BLOCKNOTE_SCHEMA,
        defaultValue,
        trailingBlock: true,
        blockNoteOptions: { autofocus: "end", ...blockNoteOptions },
    });

    useImperativeHandle(ref, () => ({ exportContent }), [exportContent]);

    const { data: { data: placeholders = {} } = {}, isLoading: isLoadingPlaceholders } = usePlaceholdersRetrieve();
    const canShowPlaceholdersMenu = !isLoadingPlaceholders && !!Object.keys(placeholders).length;

    const getPlaceholderMenuItems = (editor: BlockNoteEditor<SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema>) => {
        return Object.entries(placeholders).map(([value, label]) => ({
            title: label,
            onItemClick: () => {
                editor.insertInlineContent([{ type: "template-variable", props: { value, label } }, " "]);
            }
        }));
    };

    return (
        <>
            <BlockNoteViewField
                {...props}
                className="signature-composer"
                fullWidth
                disabled={disabled}
                composerProps={{
                    editor,
                    onChange: handleChange,
                }}
            >
                <Toolbar>
                    {canShowPlaceholdersMenu &&
                        <TemplateVariableSelector key="templateVariableSelector" variables={placeholders} isLoading={isLoadingPlaceholders} />
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
SignatureComposer.displayName = "SignatureComposer";
