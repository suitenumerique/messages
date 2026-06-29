import { Mailbox, TreeLabel, ThreadLabel, useLabelsList } from "@/features/api/gen";
import { Icon, IconType, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Field, Input, LabelledBox, useModal } from "@gouvfr-lasuite/cunningham-react";
import { useState, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "@/features/providers/mailbox";
import StringHelper from "@/features/utils/string-helper";
import { LabelModal } from "@/features/layouts/components/mailbox-panel/components/mailbox-labels/components/label-form-modal";
import { Badge } from "@/features/ui/components/badge";
import { ColorHelper } from "@/features/utils/color-helper";
import { usePopupPosition } from "@/hooks/use-popup-position";

type TagsSelectorProps = {
    mailbox: Mailbox;
    selectedTags: string[];
    onTagsChange: (tags: string[]) => void;
};

// Convert TreeLabel to ThreadLabel format for display
const treeToThreadLabel = (label: TreeLabel): ThreadLabel => ({
    id: label.id,
    name: label.name,
    slug: label.slug,
    color: label.color ?? undefined,
    display_name: label.display_name,
    description: label.description ?? undefined,
    is_auto: label.is_auto,
});

// Flatten tree labels into a list with all nested children
const flattenLabels = (labels: TreeLabel[]): TreeLabel[] => {
    const result: TreeLabel[] = [];
    const flatten = (label: TreeLabel) => {
        result.push(label);
        label.children.forEach(flatten);
    };
    labels.forEach(flatten);
    return result;
};

export const TagsSelector = ({ mailbox, selectedTags, onTagsChange }: TagsSelectorProps) => {
    const { t } = useTranslation();
    const { invalidateLabels } = useMailboxContext();
    const { open, close, isOpen } = useModal();
    const [searchQuery, setSearchQuery] = useState('');
    const [isPopupOpen, setIsPopupOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const position = usePopupPosition(containerRef, isPopupOpen, (rect) => {
        const top = rect.bottom + 4;
        const popupWidth = Math.min(400, window.innerWidth * 0.6);
        return {
            top,
            left: Math.max(8, Math.min(rect.left, window.innerWidth - popupWidth - 8)),
            maxHeight: Math.min(300, Math.max(0, window.innerHeight - top - 8)),
        };
    });

    const { data: labelsList, isLoading } = useLabelsList(
        { mailbox_id: mailbox.id },
        { query: { enabled: !!mailbox.id } }
    );

    const allLabels = useMemo(() => flattenLabels(labelsList?.data || []), [labelsList?.data]);

    const selectedLabelsAsThreadLabels = useMemo(() => {
        return allLabels
            .filter((label) => selectedTags.includes(label.id))
            .map(treeToThreadLabel);
    }, [allLabels, selectedTags]);

    const labelsOptions = useMemo(() => {
        return allLabels
            .map((label) => ({
                ...treeToThreadLabel(label),
                checked: selectedTags.includes(label.id),
            }))
            .filter((option) => {
                const normalizedLabel = StringHelper.normalizeForSearch(option.name);
                const normalizedSearchQuery = StringHelper.normalizeForSearch(searchQuery);
                return normalizedLabel.includes(normalizedSearchQuery);
            })
            .sort((a, b) => {
                if (a.checked !== b.checked) return a.checked ? -1 : 1;
                return a.name.localeCompare(b.name);
            });
    }, [allLabels, selectedTags, searchQuery]);

    const handleToggleTag = (tagId: string) => {
        if (selectedTags.includes(tagId)) {
            onTagsChange(selectedTags.filter((id) => id !== tagId));
        } else {
            onTagsChange([...selectedTags, tagId]);
        }
    };

    const handleRemoveTag = (tagId: string) => {
        onTagsChange(selectedTags.filter((id) => id !== tagId));
    };

    const handleCreateLabel = (label: { id: string }) => {
        onTagsChange([...selectedTags, label.id]);
        invalidateLabels();
    };

    const showLabelAsPlaceholder = selectedLabelsAsThreadLabels.length === 0;

    if (isLoading) {
        return (
            <div className="tags-selector tags-selector--loading">
                <Spinner size="sm" />
                <span>{t('Loading tags...')}</span>
            </div>
        );
    }

    return (
        <Field
            className="tags-selector"
            text={t('These tags will be automatically applied to every incoming message from the widget.')}
        >
            <div
                ref={containerRef}
                className="tags-selector__wrapper"
                onClick={() => setIsPopupOpen(true)}
            >
                <LabelledBox
                    label={t('Tags')}
                    labelAsPlaceholder={showLabelAsPlaceholder}
                >
                    <div className="tags-selector__value">
                        {selectedLabelsAsThreadLabels.map((label) => {
                            const badgeColor = label.color
                                ? ColorHelper.getContrastColor(label.color, {
                                    lightColor: '#fff',
                                    darkColor: '#000'
                                })
                                : undefined;
                            return (
                                <Badge
                                    key={label.id}
                                    className="label-badge label-badge--compact"
                                    style={label.color ? { backgroundColor: label.color, color: badgeColor } : undefined}
                                >
                                    <span className="label-badge__label">{label.name}</span>
                                    <button
                                        type="button"
                                        className="label-badge__remove-cta"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleRemoveTag(label.id);
                                        }}
                                        aria-label={t('Remove tag')}
                                    >
                                        <Icon name="close" size={IconSize.SMALL} type={IconType.OUTLINED} />
                                    </button>
                                </Badge>
                            );
                        })}
                    </div>
                    <div className="c__select__inner__actions">
                        <Button
                            type="button"
                            variant="tertiary"
                            size="nano"
                            onClick={(e) => {
                                e.stopPropagation();
                                setIsPopupOpen(true);
                            }}
                            icon={<Icon name="new_label" type={IconType.OUTLINED} />}
                            aria-label={t('Add tags')}
                        />
                    </div>
                </LabelledBox>
            </div>

            {isPopupOpen && position && createPortal(
                <>
                    <div className="labels-widget__popup__overlay" onClick={() => setIsPopupOpen(false)}></div>
                    <div
                        className="labels-widget__popup"
                        style={{ position: 'fixed', top: position.top, left: position.left, maxHeight: position.maxHeight }}
                    >
                        <header className="labels-widget__popup__header">
                            <h3><Icon type={IconType.OUTLINED} name="new_label" /> {t('Add tags')}</h3>
                            <Input
                                className="labels-widget__popup__search"
                                type="search"
                                icon={<Icon type={IconType.OUTLINED} name="search" />}
                                label={t('Search a tag')}
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                fullWidth
                            />
                        </header>
                        <ul className="labels-widget__popup__content">
                            {labelsOptions.map((option) => (
                                <li key={option.id}>
                                    <Checkbox
                                        checked={option.checked}
                                        onChange={() => handleToggleTag(option.id)}
                                        label={option.name}
                                    />
                                </li>
                            ))}
                            <li className="labels-widget__popup__content__empty">
                                <Button
                                    type="button"
                                    color="brand"
                                    variant="primary"
                                    onClick={open}
                                    fullWidth
                                    icon={<Icon type={IconType.OUTLINED} name="add" />}
                                >
                                    <span className="labels-widget__popup__content__empty__button-label">
                                        {searchQuery && labelsOptions.length === 0
                                            ? t('Create the label "{{label}}"', { label: searchQuery })
                                            : t('Create a new label')}
                                    </span>
                                </Button>
                                <LabelModal
                                    isOpen={isOpen}
                                    onClose={close}
                                    mailbox={mailbox}
                                    label={{ display_name: searchQuery }}
                                    onSuccess={handleCreateLabel}
                                />
                            </li>
                        </ul>
                    </div>
                </>,
                document.body
            )}
        </Field>
    );
};
