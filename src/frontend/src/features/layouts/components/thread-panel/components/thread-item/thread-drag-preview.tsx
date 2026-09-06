import clsx from "clsx";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useLayoutDragContext } from "@/features/layouts/components/layout-context";
import { isNativePlatform } from "@/features/native/platform";

type ThreadDragPreviewProps = {
    count: number;
    exiting?: boolean;
    onExitEnd?: () => void;
};

/**
 * Custom drag preview that follows the cursor.
 * The native drag image is hidden (transparent 1x1 pixel) so this component
 * acts as the visible drag feedback. It reads `dragAction` from layout context
 * to display the current drop action (e.g. "Assign + Archive").
 */
export const ThreadDragPreview = ({ count, exiting, onExitEnd }: ThreadDragPreviewProps) => {
    const { t } = useTranslation();
    const { dragAction } = useLayoutDragContext();
    const ref = useRef<HTMLSpanElement>(null);
    const isNative = isNativePlatform();

    useEffect(() => {
        // Follow the cursor and mark the whole document as a valid drop target
        // (preventDefault on dragover) to suppress Chrome's native "snap-back"
        // animation on drops outside real drop zones, which would otherwise
        // delay `dragend` by ~500-800ms and defer our exit animation.
        const handler = (e: DragEvent) => {
            e.preventDefault();
            if (ref.current) {
                if (isNative) {
                    // Above the finger centered
                    ref.current.style.left = `${e.clientX - ref.current.offsetWidth / 2}px`;
                    ref.current.style.top = `${e.clientY - ref.current.offsetHeight * 1.5}px`;
                }
                else {
                    // Right of the cursor
                    ref.current.style.left = `${e.clientX + 7}px`;
                    ref.current.style.top = `${e.clientY - 7}px`;
                }
            }
        };
        document.addEventListener('dragover', handler, true);
        return () => document.removeEventListener('dragover', handler, true);
    }, []);

    return (
        <span
            ref={ref}
            className={clsx("thread-drag-preview", { "thread-drag-preview--exiting": exiting })}
            onAnimationEnd={exiting ? onExitEnd : undefined}
        >
            {dragAction ?? t('Move {{count}} threads', {
                count: count,
                defaultValue_one: "Move {{count}} thread",
                defaultValue_other: "Move {{count}} threads"
            })}
        </span>
    )
}
