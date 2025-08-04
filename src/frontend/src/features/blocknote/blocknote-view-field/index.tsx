import { BlockSchema, InlineContentSchema, StyleSchema } from "@blocknote/core";
import { BlockNoteView } from "@blocknote/mantine";
import { Field, FieldProps } from "@openfun/cunningham-react";
import clsx from "clsx";
import { PropsWithChildren } from "react";

type BlockNoteViewFieldProps<BSchema extends BlockSchema, ISchema extends InlineContentSchema, SSchema extends StyleSchema> = PropsWithChildren<FieldProps & {
    composerProps: Parameters<typeof BlockNoteView<BSchema, ISchema, SSchema>>[0];
    disabled?: boolean;
}>
export const BlockNoteViewField = <BSchema extends BlockSchema, ISchema extends InlineContentSchema, SSchema extends StyleSchema>({ composerProps, disabled = false, children, ...fieldProps }: BlockNoteViewFieldProps<BSchema, ISchema, SSchema>) => {
    return (
        <Field {...fieldProps} className={clsx(fieldProps?.className, "composer-field", { 'composer-field--disabled': disabled })}>
            <BlockNoteView
                theme="light"
                sideMenu={false}
                slashMenu={false}
                formattingToolbar={false}
                {...composerProps}
                className={clsx(composerProps.className, "composer-field-input")}
                editable={!disabled}
            >
                {children}
            </BlockNoteView>
        </Field>
    )
}
