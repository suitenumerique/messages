import { Children, cloneElement, isValidElement, type ReactNode } from "react";

/**
 * Value the kit assigns to its "Print" entry in the viewer's header menu
 * (``FilePreview.js``, alongside ``"download"``). Not part of the public
 * ``MenuItemAction`` type, but it is the only stable, locale-independent
 * handle on that entry.
 */
const PRINT_ACTION_VALUE = "print";

const isPrintAction = (option: unknown): boolean =>
    typeof option === "object"
    && option !== null
    && (option as { value?: unknown }).value === PRINT_ACTION_VALUE;

/**
 * ``customHeaderActions`` adapter stripping the kit's own "Print" entry from
 * the FilePreview header menu.
 *
 * The kit hard-codes that entry for images and PDFs and only lets callers
 * *append* options (``headerActionsMenuOptions``). Printing has no effect in
 * the native shell (no print pipeline without a native plugin), so on native
 * the entry is removed here: the kit hands us its header node
 * (``<>{download}{info}{menu}</>``), and the menu element — the one child
 * carrying an ``options`` array — is cloned without the print option. The
 * other children (download, the sidebar "info" toggle bound to the kit's
 * internal state) are passed through untouched, which is why the header can't
 * simply be rebuilt from scratch.
 *
 * Degrades softly: an unexpected structure is rendered as-is.
 *
 * @TODO: replace with a kit prop (e.g. ``hidePrintAction``) once
 * ``@gouvfr-lasuite/ui-components`` exposes one.
 */
export const withoutPrintAction = (headerActions: ReactNode): ReactNode => {
    if (!isValidElement<{ children?: ReactNode }>(headerActions)) return headerActions;

    const children = Children.map(headerActions.props.children, (child) => {
        if (!isValidElement<{ options?: unknown }>(child) || !Array.isArray(child.props.options)) {
            return child;
        }
        return cloneElement(child, {
            options: child.props.options.filter((option) => !isPrintAction(option)),
        });
    });

    return cloneElement(headerActions, undefined, children);
};
