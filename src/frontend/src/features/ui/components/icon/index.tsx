import {
    Icon as MaterialIcon,
    IconSize,
    IconSvgProps,
    IconType,
    getIconSize,
} from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { useEffect, useReducer } from "react";

/**
 * Lazy loaders for the app-specific SVG icons. Non-eager on purpose: each
 * icon becomes its own chunk, fetched the first time it is rendered, so the
 * main bundle never embeds icons the app does not consume.
 */
const APP_ICON_MODULES = import.meta.glob<string>("./assets/*.svg", {
    query: "?raw",
    import: "default",
});

export const APP_ICON_NAMES = [
    "assign",
    "at-sign",
    "circle-dashed",
    "dns",
    "filter-notification",
    "fold",
    "forward",
    "inbox",
    "mail-open",
    "mail-out",
    "mail-plus",
    "mail-unread",
    "reply-all",
    "reply",
    "sign",
    "signature",
    "tag-fill",
    "tag",
    "text-wrap",
    "unfold",
] as const;

export type AppIconName = (typeof APP_ICON_NAMES)[number];

const isAppIconName = (name: string): name is AppIconName =>
    `./assets/${name}.svg` in APP_ICON_MODULES;

type IconCommonProps = {
    /**
     * Omit it inside a sized container (ui-kit Button icon slot…): with no
     * explicit size the icon is em-based and lets the container's font-size
     * rules drive its dimensions. An explicit size always wins.
     */
    size?: IconSize | number;
    color?: string;
    className?: string;
    "aria-label"?: string;
};

export type IconProps = IconCommonProps &
    (
        | {
              /**
               * An app icon name (rendered from our own SVG set), or any
               * Material icon name as fallback — app icons win on collision.
               */
              name: AppIconName | (string & {});
              /** Only used by the Material fallback. */
              type?: IconType;
              icon?: never;
          }
        | {
              /**
               * An SVG icon component imported from
               * `@gouvfr-lasuite/ui-kit/icons`. Passed as a component (not a
               * name) so only the icons actually imported end up in the bundle.
               */
              icon: (props: IconSvgProps) => React.JSX.Element;
              name?: never;
              type?: never;
          }
    );

/**
 * ui-kit icons ship without any ARIA attribute — a Material icon even gets
 * its ligature ("close", "search"…) read aloud. Whatever the icon source,
 * enforce the same policy: decorative by default, labelled image otherwise.
 */
const a11yProps = (ariaLabel?: string) =>
    ariaLabel
        ? ({ role: "img", "aria-label": ariaLabel } as const)
        : ({ "aria-hidden": true } as const);

/**
 * Single entry point for every icon source of the app: app-specific SVGs
 * (by name), ui-kit SVG icons (by component) and Material icons (by name,
 * as fallback). All variants share the same size/color/className API.
 */
export const Icon = (props: IconProps) => {
    const { size, color, className, "aria-label": ariaLabel } = props;

    if (props.icon) {
        const SvgIcon = props.icon;
        // With no explicit size, neutralize IconSvg's fixed width="24"
        // attributes so the icon follows the font-size context instead.
        const emSizing =
            size === undefined
                ? {
                      width: "1em",
                      height: "1em",
                  }
                : { className };
        return (
            <SvgIcon
                size={size}
                color={color}
                className={clsx("messages-icon", "messages-icon--svg", className)}
                {...emSizing}
                {...a11yProps(ariaLabel)}
            />
        );
    }

    if (isAppIconName(props.name)) {
        return (
            <AppIcon
                name={props.name}
                size={size}
                color={color}
                className={clsx("messages-icon", className)}
                aria-label={ariaLabel}
            />
        );
    }

    return (
        <MaterialIcon
            name={props.name}
            type={props.type}
            size={size}
            color={color}
            className={clsx("messages-icon", className)}
            {...a11yProps(ariaLabel)}
        />
    );
};

/**
 * Raw markup of the already fetched icons. Lets a subsequent mount of a
 * loaded icon render synchronously instead of flashing an empty placeholder.
 */
const loadedSvgs = new Map<AppIconName, string>();

type AppIconProps = IconCommonProps & { name: AppIconName };

const AppIcon = ({
    name,
    size,
    color,
    className,
    "aria-label": ariaLabel,
}: AppIconProps) => {
    const [, rerender] = useReducer((tick: number) => tick + 1, 0);
    const markup = loadedSvgs.get(name);

    useEffect(() => {
        if (loadedSvgs.has(name)) return;
        let cancelled = false;
        APP_ICON_MODULES[`./assets/${name}.svg`]()
            .then((raw) => {
                loadedSvgs.set(name, raw);
                if (!cancelled) rerender();
            })
            .catch((error: unknown) => {
                console.error(`Failed to load app icon "${name}"`, error);
            });
        return () => {
            cancelled = true;
        };
    }, [name]);

    const fontSize =
        size === undefined
            ? undefined
            : typeof size === "number"
              ? size
              : getIconSize(size);

    return (
        <span
            className={className}
            style={{ fontSize, color }}
            {...a11yProps(ariaLabel)}
            dangerouslySetInnerHTML={markup ? { __html: markup } : undefined}
        />
    );
};
