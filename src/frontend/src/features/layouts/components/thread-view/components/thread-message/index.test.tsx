import { describe, expect, it, vi, beforeEach } from "vitest"
import { createRoot, Root } from "react-dom/client"
import { act, useEffect as useEffectReal, useState as useStateReal } from "react"
import type React from "react"
import { Message } from "@/features/api/gen/models"

// This repo has no @testing-library/react; createRoot + act (both from
// React/ReactDOM, already a dependency) is the lightest way to mount a
// component tree and observe real DOM mutations driven by effects. Follows
// the same mocking approach as thread-view/index.test.tsx and
// thread-panel/components/thread-item/index.test.tsx.
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.hoisted(() => {
    if (typeof globalThis.localStorage === "undefined") {
        const store = new Map<string, string>()
        globalThis.localStorage = {
            getItem: (key: string) => store.get(key) ?? null,
            setItem: (key: string, value: string) => store.set(key, value),
            removeItem: (key: string) => store.delete(key),
            clear: () => store.clear(),
            key: (index: number) => Array.from(store.keys())[index] ?? null,
            get length() {
                return store.size
            },
        } as Storage
    }
})

vi.mock("react-i18next", () => ({
    useTranslation: () => ({
        t: (key: string) => key,
        i18n: { resolvedLanguage: "en" },
    }),
    initReactI18next: { type: "3rdParty", init: () => {} },
}))

vi.mock("@gouvfr-lasuite/ui-kit", () => ({
    Icon: ({ name, ...rest }: { name: string } & React.HTMLAttributes<HTMLSpanElement>) => (
        <span data-icon={name} {...rest} />
    ),
    IconType: { OUTLINED: "outlined", FILLED: "filled" },
    Spinner: () => <div data-testid="spinner" />,
}))

vi.mock("@/features/providers/config", () => ({
    useConfig: () => ({ MESSAGES_MANUAL_RETRY_MAX_AGE: 3600 }),
}))

vi.mock("@/features/ui/components/banner", () => ({
    Banner: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}))

vi.mock("@/features/api/gen/messages/messages", () => ({
    useMessagesDeliveryStatusesPartialUpdate: () => ({ mutate: vi.fn() }),
}))

vi.mock("@/hooks/use-ability", () => ({
    default: () => true,
    Abilities: { CAN_SEND_MESSAGES: "CAN_SEND_MESSAGES", CAN_EDIT_THREAD: "CAN_EDIT_THREAD", CAN_MANAGE_THREAD_DELIVERY_STATUSES: "CAN_MANAGE_THREAD_DELIVERY_STATUSES" },
}))

vi.mock("@/features/utils/mail-helper", () => ({
    default: {
        extractDriveAttachmentsFromHtmlBody: (content: string) => [content, []],
        extractDriveAttachmentsFromTextBody: (content: string) => [content],
    },
}))

// The real ThreadMessageBody signals readiness via `onLoad` once its iframe
// content has rendered. That readiness (not just `isFolded`) gates the
// `thread-message--folded` class, so the stub must fire `onLoad` on mount to
// keep that class meaningful for these tests.
vi.mock("./thread-message-body", () => ({
    default: function ThreadMessageBodyStub({ onLoad }: { onLoad?: () => void }) {
        useEffectReal(() => {
            onLoad?.()
        }, [onLoad])
        return <div data-testid="thread-message-body" />
    },
}))

vi.mock("../message-reply-form", () => ({
    default: () => <div data-testid="message-reply-form" />,
}))

vi.mock("./thread-message-header", () => ({
    default: () => <div data-testid="thread-message-header" />,
}))

vi.mock("./thread-message-footer", () => ({
    default: () => <div data-testid="thread-message-footer" />,
}))

vi.mock("../calendar-invite", () => ({
    CalendarInvite: () => <div data-testid="calendar-invite" />,
}))

let mailboxContextValue: Record<string, unknown> = {}
vi.mock("@/features/providers/mailbox", () => ({
    useMailboxContext: () => mailboxContextValue,
}))

// Reactive hash: tests drive it via `setLocationHash`, matching the
// `useLocation({ select })` pattern already used elsewhere in this codebase
// (e.g. mailbox.tsx, label-badge/index.tsx).
let currentHash = ""
const locationListeners = new Set<() => void>()
const setLocationHash = (hash: string) => {
    currentHash = hash
    locationListeners.forEach((listener) => listener())
}

vi.mock("@tanstack/react-router", () => ({
    useLocation: <T,>({ select }: { select: (location: { hash: string }) => T }) => {
        const [, forceRender] = useStateReal(0)
        useEffectReal(() => {
            const listener = () => forceRender((n) => n + 1)
            locationListeners.add(listener)
            return () => {
                locationListeners.delete(listener)
            }
        }, [])
        return select({ hash: currentHash })
    },
}))

const { ThreadMessage } = await import("./index")
const { default: ThreadViewProvider } = await import("../../provider")

const buildMessage = (overrides: Partial<Message> = {}): Message => ({
    id: "m1",
    subject: "Hello",
    sender: { name: "", email: "" } as Message["sender"],
    to: [],
    cc: [],
    bcc: [],
    attachments: [],
    htmlBody: [],
    textBody: [],
    is_unread: false,
    is_sender: false,
    is_draft: false,
    is_trashed: false,
    is_archived: false,
    is_starred: false,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z",
    parent_id: null,
    thread_id: "thread-1",
    ...overrides,
} as Message)

type RenderOptions = {
    hash?: string
    message: Message
    isLatest?: boolean
}

const renderThreadMessage = ({ hash = "", message, isLatest = false }: RenderOptions) => {
    currentHash = hash
    mailboxContextValue = {
        selectedMailbox: { id: "mailbox-1", email: "mailbox@example.com" },
        selectedThread: { id: "thread-1", is_spam: false },
        queryStates: { messages: { isFetching: false } },
        invalidateMailbox: vi.fn(),
        invalidateThreadsStats: vi.fn(),
    }

    const container = document.createElement("div")
    document.body.appendChild(container)
    let root: Root
    act(() => {
        root = createRoot(container)
        root.render(
            <ThreadViewProvider threadId="thread-1" messageIds={[message.id]}>
                <ThreadMessage message={message} isLatest={isLatest} />
            </ThreadViewProvider>
        )
    })

    return {
        container,
        rerender: () => {
            act(() => {
                root.render(
                    <ThreadViewProvider threadId="thread-1" messageIds={[message.id]}>
                        <ThreadMessage message={message} isLatest={isLatest} />
                    </ThreadViewProvider>
                )
            })
        },
        cleanup: () => {
            act(() => root.unmount())
            container.remove()
        },
    }
}

describe("ThreadMessage — hash-driven unfold", () => {
    beforeEach(() => {
        currentHash = ""
        locationListeners.clear()
    })

    it("unfolds when the initial hash targets this message", () => {
        // Not latest, read, no draft => starts folded by default.
        const message = buildMessage({ id: "m1", is_unread: false })
        const { container, cleanup } = renderThreadMessage({ hash: "#thread-message-m1", message, isLatest: false })

        const section = container.querySelector("#thread-message-m1")
        expect(section).not.toBeNull()
        expect(section?.classList.contains("thread-message--folded")).toBe(false)

        cleanup()
    })

    it("stays folded when the hash does not target this message", () => {
        const message = buildMessage({ id: "m1", is_unread: false })
        const { container, cleanup } = renderThreadMessage({ hash: "", message, isLatest: false })

        const section = container.querySelector("#thread-message-m1")
        expect(section?.classList.contains("thread-message--folded")).toBe(true)

        cleanup()
    })

    it("unfolds when the hash changes to target this message after mount, without remounting", () => {
        const message = buildMessage({ id: "m1", is_unread: false })
        const { container, rerender, cleanup } = renderThreadMessage({ hash: "", message, isLatest: false })

        expect(container.querySelector("#thread-message-m1")?.classList.contains("thread-message--folded")).toBe(true)

        act(() => {
            setLocationHash("#thread-message-m1")
        })
        rerender()

        expect(container.querySelector("#thread-message-m1")?.classList.contains("thread-message--folded")).toBe(false)

        cleanup()
    })
})
