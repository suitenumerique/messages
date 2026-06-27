import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { useLabelsList } from "@/features/api/gen";
import type { TreeLabel } from "@/features/api/gen/models";
import { findRootFolder } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";
import { THREAD_PANEL_FILTER_PARAMS } from "@/features/layouts/components/thread-panel/hooks/use-thread-panel-filters";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";

const findLabelBySlug = (labels: readonly TreeLabel[], slug: string): TreeLabel | undefined => {
    for (const label of labels) {
        if (label.slug === slug) return label;
        const found = findLabelBySlug(label.children, slug);
        if (found) return found;
    }
    return undefined;
};

/**
 * Name of the mailbox view the current url points to: a search, a label or a
 * folder.
 *
 * @returns The view name, or undefined while it cannot be resolved yet (label
 * still loading) or when the url filters match no known folder.
 */
export const useCurrentFolderName = (): string | undefined => {
    const { t } = useTranslation();
    const searchParams = useUrlSearchParams();
    const { selectedMailbox } = useMailboxContext();
    const labelSlug = searchParams.get('label_slug');
    const labelsQuery = useLabelsList(
        { mailbox_id: selectedMailbox?.id },
        { query: { enabled: !!selectedMailbox && !!labelSlug } }
    );

    return useMemo(() => {
        if (searchParams.has('search')) return t('folder.search', { defaultValue: 'Search' });
        if (labelSlug) return findLabelBySlug(labelsQuery.data?.data || [], labelSlug)?.display_name;
        // Thread panel filters stack on top of the folder filter — strip them
        // so the matching resolves to the underlying folder.
        const folderParams = new URLSearchParams(searchParams.toString());
        THREAD_PANEL_FILTER_PARAMS.forEach((param) => folderParams.delete(param));
        const activeFolder = findRootFolder((folder) => new URLSearchParams(folder.filter).toString() === folderParams.toString());
        return activeFolder?.name;
    }, [searchParams, labelSlug, labelsQuery.data?.data, t]);
};
