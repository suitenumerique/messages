import { BlockNoteViewField } from "@/features/blocknote/blocknote-view-field";
import { BlockNoteEditor, BlockNoteSchema, defaultInlineContentSpecs, filterSuggestionItems, PartialBlock } from "@blocknote/core";
import * as locales from '@blocknote/core/locales';
import { SuggestionMenuController, useCreateBlockNote } from "@blocknote/react";
import { FieldProps } from "@openfun/cunningham-react";
import { useEffect } from "react";
import { useFormContext } from "react-hook-form";
import { useTranslation } from "react-i18next";

import { InlineTemplateVariable, TemplateVariableSelector } from "@/features/blocknote/inline-template-variable";
import { Toolbar } from "@/features/blocknote/toolbar";
import { usePlaceholdersRetrieve } from "@/features/api/gen";
import MailHelper from "@/features/utils/mail-helper";

const SIGNATURE_BLOCKNOTE_SCHEMA = BlockNoteSchema.create({
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
    blockNoteOptions?: Partial<typeof SIGNATURE_BLOCKNOTE_SCHEMA>
    defaultValue?: string | null;
    disabled?: boolean;
}

/**
 * The composer component for the signature content.
 */
export const SignatureComposer = ({ blockNoteOptions, defaultValue, disabled = false, ...props }: SignatureComposerProps) => {
    const { t, i18n } = useTranslation();
    const form = useFormContext();
    const { data: { data: placeholders = {} } = {}, isLoading: isLoadingPlaceholders } = usePlaceholdersRetrieve();
    const canShowPlaceholdersMenu = !isLoadingPlaceholders && !!placeholders;

    const editor = useCreateBlockNote({
        schema: SIGNATURE_BLOCKNOTE_SCHEMA,
        tabBehavior: "prefer-navigate-ui",
        initialContent: defaultValue ? JSON.parse(defaultValue): [{ type: "paragraph", content: "" }],
        trailingBlock: false,
        dictionary: {
            ...(locales[(i18n.resolvedLanguage) as keyof typeof locales] || locales.en),
            placeholders: {
                ...(locales[(i18n.resolvedLanguage) as keyof typeof locales] || locales.en).placeholders,
                emptyDocument: t('Start typing...'),
                default: t('Start typing...'),
            }
        },
        ...blockNoteOptions,
    }, [i18n.resolvedLanguage]);

    const handleChange = async () => {
        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const html = await MailHelper.markdownToHtml(markdown);
        form.setValue("rawBody", JSON.stringify(editor.document), { shouldDirty: true });
        form.setValue("textBody", markdown);
        form.setValue("htmlBody", html);
    }

    const getPlaceholderMenuItems = (editor: BlockNoteEditor<SignatureComposerBlockSchema, SignatureComposerInlineContentSchema, SignatureComposerStyleSchema>) => {
        return Object.entries(placeholders).map(([value, label]) => ({
            title: label,
            onItemClick: () => {
                editor.insertInlineContent([{ type: "template-variable", props: { value: value, label: label } }, " "]);
            }
        }));
    }


    useEffect(() => {
        handleChange();
    }, [])

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
                        <TemplateVariableSelector key={"templateVariableSelector"} variables={placeholders} isLoading={isLoadingPlaceholders} />
                    }
                </Toolbar>
                {canShowPlaceholdersMenu &&
                    <SuggestionMenuController
                        triggerCharacter="{"
                        getItems={async (query) => filterSuggestionItems(getPlaceholderMenuItems(editor), query)}
                    />
                }
            </BlockNoteViewField>
            <input {...form.register("htmlBody")} type="hidden" />
            <input {...form.register("textBody")} type="hidden" />
            <input {...form.register("rawBody")} type="hidden" />
        </>
    )
};
