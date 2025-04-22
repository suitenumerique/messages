import { Badge } from "@/features/ui/components/badge"
import Link from "next/link"
import { useParams } from "next/navigation"
import { useRouter } from "next/router"
import { useEffect } from "react"

// @TODO: replace with real data when folder will be ready
type HardcodedMailbox = {
    id: string;
    name: string;
    icon: string;
    unread: number;
}

const TMP_MAILBOXES: HardcodedMailbox[] = [
    {
        id: "0",
        name: "Tous les messages",
        icon: "folder",
        unread: 10,
    },
    {
        id: "1",
        name: "Boîte de réception",
        icon: "inbox",
        unread: 0,
    },
    {
        id: "2",
        name: "Brouillons",
        icon: "drafts",
        unread: 2,
    },
    {
        id: "3",
        name: "Envoyés",
        icon: "outbox",
        unread: 0,
    },
    {
        id: "4",
        name: "Pourriels",
        icon: "report",
        unread: 0,
    },
    {
        id: "5",
        name: "Archives",
        icon: "inventory_2",
        unread: 0,
    },
    {
        id: "6",
        name: "Corbeille",
        icon: "delete",
        unread: 0,
    }
]

export const MailboxList = () => {
    const defaultMailboxId = TMP_MAILBOXES[0].id;
    const router = useRouter()

    useEffect(() => {
        if (router.pathname === "/") {
            router.push(`/mailbox/${defaultMailboxId}`)
        }
    }, [])

    return (
        <div className="mailbox-list">
            {/* TODO: replace with real data */}
            {TMP_MAILBOXES.map((mailbox) => (
                <MailboxListItem
                    key={mailbox.id}
                    mailbox={mailbox}
                />
            ))}
        </div>
    )
}

type MailboxListItemProps = {
    mailbox: HardcodedMailbox
}

const MailboxListItem = ({ mailbox }: MailboxListItemProps) => {
    const params = useParams<{ mailboxId?: string }>()
    return (
        <Link
            href={`/mailbox/${mailbox.id}`}
            className={`mailbox__item ${mailbox.id === params?.mailboxId ? "mailbox__item--active" : ""}`}
        >
            <p className="mailbox__item-label">
                <span className="material-icons" aria-hidden="true">{mailbox.icon}</span>
                {mailbox.name}
            </p>
            {mailbox.unread > 0 && <Badge>{mailbox.unread}</Badge>}
        </Link>
    )
}