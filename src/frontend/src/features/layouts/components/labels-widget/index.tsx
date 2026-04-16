import { ThreadLabel, TreeLabel, useLabelsAddThreadsCreate, useLabelsList, useLabelsRemoveThreadsCreate } from "@/features/api/gen";
import { Thread } from "@/features/api/gen/models";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Input, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { RefObject, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import StringHelper from "@/features/utils/string-helper";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { usePopupPosition } from "@/hooks/use-popup-position";
import { LabelModal } from "@/features/layouts/components/mailbox-panel/components/mailbox-labels/components/label-form-modal";

type LabelsWidgetProps = {
    threadIds: string[];
    // Fallback for a deep-linked single thread that is not in `threads.results`
    // yet (e.g. filter active). Lets the popup display the right checked state.
    initialLabels?: readonly ThreadLabel[];
}

type CreateModalState = {
    isOpen: boolean;
    initialName: string;
}

export const LabelsWidget = ({ threadIds, initialLabels }: LabelsWidgetProps) => {
    const { t } = useTranslation();
    const { selectedMailbox, threads, invalidateThreadMessages } = useMailboxContext();
    const canManageLabels = useAbility(Abilities.CAN_MANAGE_MAILBOX_LABELS, selectedMailbox);
    const { data: labelsList, isLoading: isLoadingLabelsList } = useLabelsList(
        { mailbox_id: selectedMailbox!.id },
        { query: { enabled: canManageLabels } }
    );
    const [isPopupOpen, setIsPopupOpen] = useState(false);
    const [createModal, setCreateModal] = useState<CreateModalState>({ isOpen: false, initialName: '' });
    const anchorRef = useRef<HTMLDivElement>(null);

    const addLabelMutation = useLabelsAddThreadsCreate({
        mutation: { onSuccess: () => invalidateThreadMessages() }
    });
    const deleteLabelMutation = useLabelsRemoveThreadsCreate({
        mutation: { onSuccess: () => invalidateThreadMessages() }
    });

    const handleAddLabel = (labelId: string) => {
        addLabelMutation.mutate({
            id: labelId,
            data: { thread_ids: threadIds },
        });
    }
    const handleDeleteLabel = (labelId: string) => {
        deleteLabelMutation.mutate({
            id: labelId,
            data: { thread_ids: threadIds },
        });
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
                        icon={<Icon type={IconType.OUTLINED} name="new_label" />}
                    />
                </Tooltip>
            </div>
        );
    }

    return (
        <div className="labels-widget" ref={anchorRef}>
            <Tooltip content={t('Add label')}>
                <Button
                    onClick={() => setIsPopupOpen(true)}
                    size="nano"
                    variant="tertiary"
                    aria-label={t('Add label')}
                    disabled={threadIds.length === 0}
                    icon={<Icon type={IconType.OUTLINED} name="new_label" />}
                />
            </Tooltip>
            {isPopupOpen && (
                <LabelsPopup
                    anchorRef={anchorRef}
                    onClose={() => setIsPopupOpen(false)}
                    labels={labelsList!.data || []}
                    threadIds={threadIds}
                    labelCounts={labelCounts}
                    onAddLabel={handleAddLabel}
                    onDeleteLabel={handleDeleteLabel}
                    onCreateLabel={(initialName) => setCreateModal({ isOpen: true, initialName })}
                    closeOnEsc={!createModal.isOpen}
                />
            )}
            <LabelModal
                isOpen={createModal.isOpen}
                onClose={() => setCreateModal((s) => ({ ...s, isOpen: false }))}
                label={{ display_name: createModal.initialName }}
                onSuccess={(label) => handleAddLabel(label.id)}
            />
        </div>
    );
};

export type LabelsPopupProps = {
    labels: TreeLabel[];
    threadIds: string[];
    labelCounts: Map<string, number>;
    anchorRef: RefObject<HTMLElement | null>;
    onClose: () => void;
    onAddLabel: (labelId: string) => void;
    onDeleteLabel: (labelId: string) => void;
    onCreateLabel: (initialName: string) => void;
    // Set to false when a modal stacked above should own Escape — otherwise
    // the popup's capture-phase listener races with the modal's and both close.
    closeOnEsc?: boolean;
}

type LabelOption = {
    label: string;
    value: string;
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
    closeOnEsc = true,
}: LabelsPopupProps) => {
    const { t } = useTranslation();
    const [searchQuery, setSearchQuery] = useState('');
    const totalThreads = threadIds.length;
    const position = usePopupPosition(anchorRef, true, (rect) => {
        const top = rect.bottom + 4;
        return {
            top,
            right: Math.max(8, window.innerWidth - rect.right - 100),
            maxHeight: Math.min(300, Math.max(0, window.innerHeight - top - 8)),
        };
    });

    useEffect(() => {
        if (!closeOnEsc) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape') return;
            e.stopImmediatePropagation();
            onClose();
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [onClose, closeOnEsc]);

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
            onDeleteLabel(option.value);
        } else {
            onAddLabel(option.value);
        }
    }

    if (!position) return null;

    // Portal into #__next rather than document.body so the popup shares the
    // same stacking context as Cunningham's Modal (rendered via ModalProvider
    // inside #__next). Portalling to body places the popup on a higher paint
    // layer than anything isolated inside #__next, regardless of z-index.
    const portalTarget = document.getElementById('__next') ?? document.body;

    return createPortal(
        <>
            <div className="labels-widget__popup__overlay" onClick={onClose}></div>
            <div
                className="labels-widget__popup"
                role="dialog"
                aria-modal="true"
                aria-label={t('Add labels')}
                style={{
                    position: 'fixed',
                    top: position.top,
                    right: position.right,
                    maxHeight: position.maxHeight,
                }}
            >
                <header className="labels-widget__popup__header">
                    <h3><Icon type={IconType.OUTLINED} name="new_label" /> {t('Add labels')}</h3>
                    <Input
                        className="labels-widget__popup__search"
                        type="search"
                        icon={<Icon type={IconType.OUTLINED} name="search" />}
                        label={t('Search a label')}
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        fullWidth
                    />
                </header>
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
                        <Button color="brand" variant="primary" onClick={() => onCreateLabel(searchQuery)} fullWidth icon={<Icon type={IconType.OUTLINED} name="add" />}>
                            <span className="labels-widget__popup__content__empty__button-label">
                            {searchQuery && labelsOptions.length === 0 ? t('Create the label "{{label}}"', { label: searchQuery }) : t('Create a new label')}
                            </span>
                        </Button>
                    </li>
                </ul>
            </div>
        </>,
        portalTarget
    );
};
