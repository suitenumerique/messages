import { ThreadLabel, TreeLabel, useLabelsAddThreadsCreate, useLabelsList, useLabelsRemoveThreadsCreate } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import StringHelper from "@/features/utils/string-helper";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Input, Tooltip } from "@openfun/cunningham-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

type ThreadLabelsWidgetProps = {
    selectedLabels: readonly ThreadLabel[];
    threadId: string;
}

export const ThreadLabelsWidget = ({ threadId, selectedLabels = [] }: ThreadLabelsWidgetProps) => {
    const { t } = useTranslation();
    const {data: labelsList, isLoading: isLoadingLabelsList } = useLabelsList();
    const [isPopupOpen, setIsPopupOpen] = useState(false);

    if (isLoadingLabelsList) {
        return (
            <div className="thread-labels-widget" aria-busy={true}>
                <Tooltip
                    content={
                        <span className="thread-labels-widget__loading-labels-tooltip-content">
                            <Spinner size="sm" />
                            {t('thread-view.action-bar.loading-labels')}
                        </span>
                    }
                >
                    <Button size="small" color="primary-text">
                        <Icon type={IconType.OUTLINED} name="new_label" />
                    </Button>
                </Tooltip>
            </div>
        )
    }

    return (
        <div className="thread-labels-widget">
            <Tooltip content={t('actions.add_label')}>
                <Button onClick={() => setIsPopupOpen(true)} size="small" color="primary-text">
                    <Icon type={IconType.OUTLINED} name="new_label" />
                </Button>
            </Tooltip>
            {isPopupOpen &&
            <>
                <LabelsPopup
                    labels={labelsList!.data || []}
                    selectedLabels={selectedLabels}
                    threadId={threadId}
                />
                <div className="thread-labels-widget__popup__overlay" onClick={() => setIsPopupOpen(false)}></div>
            </>
            }
        </div>
    );
};

type LabelsPopupProps = {
    labels: TreeLabel[];
    threadId: string;
    selectedLabels: readonly ThreadLabel[];
}

const LabelsPopup = ({ labels = [], selectedLabels, threadId }: LabelsPopupProps) => {
    const { t } = useTranslation();
    const [searchQuery, setSearchQuery] = useState('');
    const { invalidateThreadMessages } = useMailboxContext();
    const getFlattenLabelOptions = (label: TreeLabel, level: number = 0): Array<{label: string, value: string, checked: boolean}> => {
        let children: Array<{label: string, value: string, checked: boolean}> = [];
        if (label.children.length > 0) {
            children = label.children.map((child) => getFlattenLabelOptions(child, level + 1)).flat();
        }
        return [{
            label: label.name,
            value: label.id,
            checked: selectedLabels.some((selectedLabel) => selectedLabel.id === label.id),
        }, ...children];
    }
    const labelsOptions = useMemo(() => {
        return labels
        .map((label) => getFlattenLabelOptions(label))
        .flat()
        .filter((option) =>
            StringHelper
                .normalizeForSearch(option.label)
                .includes(searchQuery)
        )
        .sort((a, b) => {
            if (a.checked !== b.checked) return a.checked ? -1 : 1;
            return a.label.localeCompare(b.label);
        });
    }, [labels, searchQuery]);

    const addLabelMutation = useLabelsAddThreadsCreate({
        mutation: {
            onSuccess: (_, variables) => {
                invalidateThreadMessages();
                labelsOptions.find((option) => option.value === variables.id)!.checked = true;
            }
        }
    });
    const deleteLabelMutation = useLabelsRemoveThreadsCreate({
        mutation: {
            onSuccess: (_, variables) => {
                invalidateThreadMessages();
                labelsOptions.find((option) => option.value === variables.id)!.checked = false;
            }
        }
    });

    const handleAddLabel = (labelId: string) => {
        addLabelMutation.mutate({
            id: labelId,
            data: {
                thread_ids: [threadId],
            },
        });
    }
    const handleDeleteLabel = (labelId: string) => {
        deleteLabelMutation.mutate({
            id: labelId,
            data: {
                thread_ids: [threadId],
            },
        });
    }

    return (
        <div className="thread-labels-widget__popup">
            <header className="thread-labels-widget__popup__header">
                <h3><Icon type={IconType.OUTLINED} name="new_label" /> {t('thread-labels-widget.popup.title')}</h3>
                <Input
                    className="thread-labels-widget__popup__search"
                    type="search"
                    icon={<Icon type={IconType.OUTLINED} name="search" />}
                    label={t('thread-labels-widget.popup.search_placeholder')}
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(StringHelper.normalizeForSearch(e.target.value))}
                    fullWidth
                />
            </header>
            <ul className="thread-labels-widget__popup__content">
                {labelsOptions.map((option) => (
                    <li key={option.value}>
                        <Checkbox
                            checked={option.checked}
                            onChange={() => option.checked ? handleDeleteLabel(option.value) : handleAddLabel(option.value)}
                            label={option.label}
                        />
                    </li>
                ))}
            </ul>
        </div>
    );
};

LabelsPopup.displayName = 'LabelsPopup';
