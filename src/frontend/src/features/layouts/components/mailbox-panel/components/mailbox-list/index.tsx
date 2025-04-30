import clsx from "clsx"
import Link from "next/link"
import { useParams, useSearchParams } from "next/navigation"
import { useMemo } from "react"

// @TODO: replace with real data when folder will be ready
type Folder = {
    name: string;
    icon: string;
    filter?: Record<string, string>;
}

export const DEFAULT_FOLDERS: Folder[] = [
    {
        name: "Tous les messages",
        icon: "folder",
        filter: {
            has_trashed: "0"
        },
    },
    // {
    //     name: "Boîte de réception",
    //     icon: "inbox",
    //     filter: {
    //         has_unread: "1",
    //     },
    // },
    {
        name: "Brouillons",
        icon: "drafts",
        filter: {
            has_draft: "1",
        },
    },
    {
        name: "Envoyés",
        icon: "outbox",
        filter: {
            has_sender: "1",
        },
    },
    {
        name: "Corbeille",
        icon: "delete",
        filter: {
            has_trashed: "1",
        },
    }
    // {
    //     name: "Pourriels",
    //     icon: "report",
    // },
    // {
    //     name: "Archives",
    //     icon: "inventory_2",
    // },
]

export const MailboxList = () => {
    return (
        <div className="mailbox-list">
            {DEFAULT_FOLDERS.map((folder) => (
                <FolderItem
                    key={folder.icon}
                    folder={folder}
                />
            ))}
        </div>
    )
}

type FolderItemProps = {
    folder: Folder
}

const FolderItem = ({ folder }: FolderItemProps) => {
    const params = useParams<{ mailboxId?: string }>()
    const searchParams = useSearchParams()
    const queryParams = useMemo(() => {
        const params = new URLSearchParams(Object.entries(folder.filter || {}));
        return params.toString();
    }, [folder.filter]);

    const isActive = useMemo(() => {
        const folderFilter = Object.entries(folder.filter || {});
        if (folderFilter.length !== searchParams.size) return false;

        return folderFilter.every(([key, value]) => {
            return searchParams.get(key) === value;
        });
        
        
    }, [searchParams, folder.filter]);

    return (
        <Link
            href={`/mailbox/${params?.mailboxId}?${queryParams}`}
            className={clsx("mailbox__item", {
                "mailbox__item--active": isActive
            })}
        >
            <p className="mailbox__item-label">
                <span className="material-icons" aria-hidden="true">{folder.icon}</span>
                {folder.name}
            </p>
            {/* {mailbox.unread > 0 && <Badge>{mailbox.unread}</Badge>} */}
        </Link>
    )
}