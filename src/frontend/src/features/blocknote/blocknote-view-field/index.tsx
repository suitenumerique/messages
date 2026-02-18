import { BlockSchema, InlineContentSchema, StyleSchema } from "@blocknote/core";
import { BlockNoteView } from "@blocknote/mantine";
import { FilePanelController } from "@blocknote/react";
import { Field, FieldProps } from "@gouvfr-lasuite/cunningham-react";
import clsx from "clsx";
import { PropsWithChildren } from "react";
import { createPortal } from "react-dom";

import { CustomSideMenuController } from "../custom-side-menu";
import { CustomSlashMenu } from "../custom-slash-menu";

type BlockNoteViewFieldProps<BSchema extends BlockSchema, ISchema extends InlineContentSchema, SSchema extends StyleSchema> = PropsWithChildren<FieldProps & {
    composerProps: Parameters<typeof BlockNoteView<BSchema, ISchema, SSchema>>[0];
    disabled?: boolean;
}>
export const BlockNoteViewField = <BSchema extends BlockSchema, ISchema extends InlineContentSchema, SSchema extends StyleSchema>({ composerProps, disabled = false, children, ...fieldProps }: BlockNoteViewFieldProps<BSchema, ISchema, SSchema>) => {
    return (
        <Field
            {...fieldProps}
            className={clsx(fieldProps?.className, "composer-field", { 'composer-field--disabled': disabled })}
        >
            <BlockNoteView
                theme="light"
                sideMenu={false}
                slashMenu={false}
                formattingToolbar={false}
                filePanel={false}
                {...composerProps}
                className={clsx(composerProps.className, "composer-field-input")}
                editable={!disabled}
            >
                <CustomSideMenuController />
                <CustomSlashMenu />
                <PortalledFilePanel />
                {children}
            </BlockNoteView>
        </Field>
    )
}

/**
 * Renders the BlockNote file panel (image upload popover) in a React portal
 * at document.body level. This prevents the popover from being clipped by
 * ancestor overflow containers (e.g. modal scrollers).
 */
const PortalledFilePanel = () => {
    return createPortal(
        <div className="bn-container bn-mantine" data-mantine-color-scheme="light">
            <FilePanelController />
        </div>,
        document.body,
    );
}
