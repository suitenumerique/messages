"""AI-powered label assignment for threads."""

from core.ai.thread_classifier import get_most_relevant_labels
from core.models import Label, Thread


def assign_label_to_thread(thread: Thread, mailbox_id):
    """Assign relevant labels to a thread based on AI classification."""

    labels = Label.objects.filter(mailbox_id=mailbox_id, is_auto=True).values()
    best_labels = get_most_relevant_labels(thread, labels)

    for label_name in best_labels:
        matching_label = next(
            (label for label in labels if label["name"] == label_name), None
        )

        if matching_label:
            thread.labels.add(matching_label["id"])
