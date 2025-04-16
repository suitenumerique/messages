import { Badge } from "@/features/ui/components/badge"
import Link from "next/link"
import { useParams } from "next/navigation"
import { useRouter } from "next/router"
import { useEffect } from "react"

const TMP_MAILBOXES = [
    {
        id: "1",
        name: "Boîte de réception",
        icon: "inbox",
        unread: 10,
        total: 2000,
    },
    {
        id: "2",
        name: "Brouillons",
        icon: "drafts",
        unread: 2000,
        total: 2000,
    },
    {
        id: "3",
        name: "Envoyés",
        icon: "outbox",
        unread: 10,
        total: 2000,
    },
    {
        id: "4",
        name: "Pourriels",
        icon: "report",
        unread: 10,
        total: 2000,
    },
    {
        id: "5",
        name: "Archives",
        icon: "inventory_2",
        unread: 10,
        total: 2000,
    },
    {
        id: "6",
        name: "Corbeille",
        icon: "delete",
        unread: 10,
        total: 2000,
    }
]

export const MailboxList = () => {
    const defaultMailboxId = TMP_MAILBOXES[0].id;
    const params = useParams<{ mailboxId: string }>()
    const router = useRouter()

    useEffect(() => {
        if (!params.mailboxId) {
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

const MailboxListItem = ({ mailbox }) => {
    const { mailboxId } = useParams<{ mailboxId: string }>()
    return (
        <Link
            href={`/mailbox/${mailbox.id}`}
            className={`mailbox__item ${mailbox.id === mailboxId ? "mailbox__item--active" : ""}`}
        >
            <p className="mailbox__item-label">
                <span className="material-icons" aria-hidden="true">{mailbox.icon}</span>
                {mailbox.name}
            </p>
            <Badge>{mailbox.unread}</Badge>
        </Link>
    )
}