import { Label, ThreadLabel, TreeLabel, useLabelsList } from "@/features/api/gen";
import { Thread } from "@/features/api/gen/models";
import { Spinner, useResponsive } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Input, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { forwardRef, RefObject, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Drawer } from "@/features/ui/components/drawer";
import { useMailboxContext } from "@/features/providers/mailbox";
import StringHelper from "@/features/utils/string-helper";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { usePopupPosition } from "@/hooks/use-popup-position";
import { LabelModal } from "@/features/layouts/components/mailbox-panel/components/mailbox-labels/components/label-form-modal";
import useDeleteLabel from "@/features/message/use-delete-label";
import useAddLabel from "@/features/message/use-add-label";
import { Icon } from "@/features/ui/components/icon";
import { Plus, TagAdd, Zoom } from "@gouvfr-lasuite/ui-kit/icons";

export type LabelsWidgetHandle = {
    open: () => void;
};

type LabelsWidgetProps = {
    threadIds: string[];
    // Fallback for a deep-linked single thread that is not in `threads.results`
    // yet (e.g. filter active). Lets the popup display the right checked state.
    initialLabels?: readonly ThreadLabel[];
    /**
     * Render only the popup/drawer, without the "Add label" trigger button.
     * Used on mobile where the widget is opened through the `open()` handle
     * (more-options drawer) instead of a dedicated button.
     */
    hideTrigger?: boolean;
}

type CreateModalState = {
    isOpen: boolean;
    initialName: string;
}

// Project either a TreeLabel (from the labels list) or a Label (from the
// create endpoint) onto the ThreadLabel shape stored on `Thread.labels`.
const toThreadLabel = (label: TreeLabel | Label): ThreadLabel => ({
    id: label.id,
    name: label.name,
    slug: label.slug,
    color: label.color,
    display_name: label.display_name,
    description: label.description,
    is_auto: label.is_auto,
});

const findTreeLabelById = (labels: readonly TreeLabel[], id: string): TreeLabel | undefined => {
    for (const label of labels) {
        if (label.id === id) return label;
        const found = findTreeLabelById(label.children, id);
        if (found) return found;
    }
    return undefined;
};

export const LabelsWidget = forwardRef<LabelsWidgetHandle, LabelsWidgetProps>(
    function LabelsWidget({ threadIds, initialLabels, hideTrigger = false }, ref) {
    const { t } = useTranslation();
    const { selectedMailbox, threads } = useMailboxContext();
    const canManageLabels = useAbility(Abilities.CAN_MANAGE_MAILBOX_LABELS, selectedMailbox);
    const { data: labelsList, isLoading: isLoadingLabelsList } = useLabelsList(
        { mailbox_id: selectedMailbox!.id },
        { query: { enabled: canManageLabels } }
    );
    const [isPopupOpen, setIsPopupOpen] = useState(false);
    const [createModal, setCreateModal] = useState<CreateModalState>({ isOpen: false, initialName: '' });
    const anchorRef = useRef<HTMLDivElement>(null);

    useImperativeHandle(ref, () => ({
        open: () => setIsPopupOpen(true),
    }), []);

    const { addLabel } = useAddLabel();
    const { deleteLabel } = useDeleteLabel();

    const handleAddLabel = (labelId: string) => {
        const treeLabel = findTreeLabelById(labelsList?.data ?? [], labelId);
        if (!treeLabel) return;
        addLabel({ label: toThreadLabel(treeLabel), threadIds });
    }
    const handleDeleteLabel = (labelId: string, labelSlug: string) => {
        deleteLabel({ labelId, labelSlug, threadIds });
    }

    const labelCounts = useMemo(() => {
        const counts = new Map<string, number>();
        const fromThreads = threads?.results.filter((thread: Thread) => threadIds.includes(thread.id)) ?? [];
        if (fromThreads.length > 0) {
            for (const thread of fromThreads) {
                for (const label of thread.labels) {
                    counts.set(label.id, (counts.get(label.id) ?? 0) + 1);
                }
            }
        } else if (initialLabels && threadIds.length === 1) {
            for (const label of initialLabels) {
                counts.set(label.id, 1);
            }
        }
        return counts;
    }, [threads?.results, threadIds, initialLabels]);

    if (!canManageLabels) return null;

    if (isLoadingLabelsList) {
        if (hideTrigger) return null;
        return (
            <div className="labels-widget" aria-busy={true}>
                <Tooltip
                    content={
                        <span className="labels-widget__loading-labels-tooltip-content">
                            <Spinner size="sm" />
                            {t('Loading labels...')}
                        </span>
                    }
                >
                    <Button
                        size="nano"
                        variant="tertiary"
                        aria-label={t('Add label')}
                        icon={<Icon icon={TagAdd} />}
                    />
                </Tooltip>
            </div>
        );
    }

    return (
        <div className="labels-widget" ref={anchorRef}>
            {!hideTrigger && (
                <Tooltip content={t('Add label')}>
                    <Button
                        onClick={() => setIsPopupOpen(true)}
                        size="nano"
                        variant="tertiary"
                        aria-label={t('Add label')}
                        disabled={threadIds.length === 0}
                        icon={<Icon icon={TagAdd} />}
                    />
                </Tooltip>
            )}
            {isPopupOpen && (
                <LabelsPopup
                    anchorRef={anchorRef}
                    onClose={() => setIsPopupOpen(false)}
                    labels={labelsList!.data || []}
                    threadIds={threadIds}
                    labelCounts={labelCounts}
                    onAddLabel={handleAddLabel}
                    onDeleteLabel={handleDeleteLabel}
                    // Closing before opening the modal: Cunningham modals carry
                    // no z-index (react-modal portaled to body), so the popup
                    // and its overlay would paint above the modal and swallow
                    // its clicks. The created label is applied by `onSuccess`,
                    // so the popup has nothing left to show anyway.
                    onCreateLabel={(initialName) => {
                        setIsPopupOpen(false);
                        setCreateModal({ isOpen: true, initialName });
                    }}
                />
            )}
            <LabelModal
                isOpen={createModal.isOpen}
                onClose={() => setCreateModal((s) => ({ ...s, isOpen: false }))}
                label={{ display_name: createModal.initialName }}
                onSuccess={(label) => addLabel({ label: toThreadLabel(label), threadIds })}
            />
        </div>
    );
});

export type LabelsPopupProps = {
    labels: TreeLabel[];
    threadIds: string[];
    labelCounts: Map<string, number>;
    anchorRef: RefObject<HTMLElement | null>;
    onClose: () => void;
    onAddLabel: (labelId: string) => void;
    onDeleteLabel: (labelId: string, labelSlug: string) => void;
    onCreateLabel: (initialName: string) => void;
}

type LabelOption = {
    label: string;
    value: string;
    slug: string;
    checked: boolean;
    indeterminate: boolean;
}

export const LabelsPopup = ({
    labels = [],
    threadIds,
    labelCounts,
    anchorRef,
    onClose,
    onAddLabel,
    onDeleteLabel,
    onCreateLabel,
}: LabelsPopupProps) => {
    const { t } = useTranslation();
    const { isMobile } = useResponsive();
    const [searchQuery, setSearchQuery] = useState('');
    const totalThreads = threadIds.length;
    // On mobile the widget renders as a bottom drawer instead: an anchored
    // popup is cramped and half-covered by the keyboard there.
    const position = usePopupPosition(anchorRef, !isMobile, (rect) => {
        const top = rect.bottom + 4;
        return {
            top,
            right: Math.max(8, window.innerWidth - rect.right - 100),
            maxHeight: Math.min(300, Math.max(0, window.innerHeight - top - 8)),
        };
    });

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape') return;
            e.stopImmediatePropagation();
            onClose();
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [onClose]);

    const getFlattenLabelOptions = (label: TreeLabel): LabelOption[] => {
        const children: LabelOption[] = label.children.length > 0
            ? label.children.flatMap((child) => getFlattenLabelOptions(child))
            : [];

        const count = labelCounts.get(label.id) ?? 0;
        const checked = totalThreads > 0 && count === totalThreads;
        const indeterminate = count > 0 && count < totalThreads;

        return [{
            label: label.name,
            value: label.id,
            slug: label.slug,
            checked,
            indeterminate,
        }, ...children];
    }

    const labelsOptions = labels
        .flatMap((label) => getFlattenLabelOptions(label))
        .filter((option) => {
            const normalizedLabel = StringHelper.normalizeForSearch(option.label);
            const normalizedSearchQuery = StringHelper.normalizeForSearch(searchQuery);
            return normalizedLabel.includes(normalizedSearchQuery);
        })
        .sort((a, b) => {
            if (a.checked !== b.checked) return a.checked ? -1 : 1;
            if (a.indeterminate !== b.indeterminate) return a.indeterminate ? -1 : 1;
            return a.label.localeCompare(b.label);
        });

    const handleToggle = (option: LabelOption) => {
        if (option.checked) {
            onDeleteLabel(option.value, option.slug);
        } else {
            onAddLabel(option.value);
        }
    }

    if (!isMobile && !position) return null;

    const searchInput = (
        <Input
            className="labels-widget__popup__search"
            type="search"
            icon={<Icon icon={Zoom} />}
            label={t('Search a label')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            fullWidth
        />
    );

    const optionsList = (
        <ul className="labels-widget__popup__content">
            {labelsOptions.map((option) => (
                <li key={option.value}>
                    <Checkbox
                        checked={option.checked}
                        indeterminate={option.indeterminate}
                        onChange={() => handleToggle(option)}
                        label={option.label}
                    />
                </li>
            ))}
            <li className="labels-widget__popup__content__empty">
                <Button color="brand" variant="primary" onClick={() => onCreateLabel(searchQuery)} fullWidth icon={<Icon icon={Plus} />}>
                    <span className="labels-widget__popup__content__empty__button-label">
                    {searchQuery && labelsOptions.length === 0 ? t('Create the label "{{label}}"', { label: searchQuery }) : t('Create a new label')}
                    </span>
                </Button>
            </li>
        </ul>
    );

    return createPortal(
        isMobile ? (
            <>
                <div
                    className="labels-widget__popup__overlay labels-widget__popup__overlay--scrim"
                    onClick={onClose}
                ></div>
                <div className="labels-widget__drawer">
                    <Drawer title={t('Add labels')} onClose={onClose}>
                        <div className="labels-widget__drawer__search">{searchInput}</div>
                        {optionsList}
                    </Drawer>
                </div>
            </>
        ) : (
            <>
                <div className="labels-widget__popup__overlay" onClick={onClose}></div>
                <div
                    className="labels-widget__popup"
                    role="dialog"
                    aria-modal="true"
                    aria-label={t('Add labels')}
                    style={{
                        position: 'fixed',
                        top: position?.top,
                        right: position?.right,
                        maxHeight: position?.maxHeight,
                    }}
                >
                    <header className="labels-widget__popup__header">
                        <h3><Icon icon={TagAdd} /> {t('Add labels')}</h3>
                        {searchInput}
                    </header>
                    {optionsList}
                </div>
            </>
        ),
        document.body
    );
};
