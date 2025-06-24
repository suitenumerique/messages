import { ThreadLabel } from "@/features/api/gen/models/thread_label";
import { LabelBadge } from "@/features/ui/components/label-badge";

type ThreadViewLabelsListProps = {
    labels: readonly ThreadLabel[];
}

/**
 * List of labels for a thread.
 */
export const ThreadViewLabelsList = ({ labels }: ThreadViewLabelsListProps) => {
    return <div className="thread-view__labels-list">
        {labels.map((label) => (
            <LabelBadge key={label.id} label={label} removable linkable />
        ))}
    </div>;
};
