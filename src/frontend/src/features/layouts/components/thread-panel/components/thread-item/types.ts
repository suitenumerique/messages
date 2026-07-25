export type MessageSummary = {
    id: string;
    sender: {
        id: string;
        name: string;
        email: string;
    };
    sent_at: string | null;
    is_unread: boolean;
    is_draft: boolean;
    has_attachments: boolean;
    snippet: string;
};
