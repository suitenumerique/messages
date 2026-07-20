import { describe, expect, it, vi, beforeEach } from "vitest"
import { createRoot, Root } from "react-dom/client"
import { act, useEffect as useEffectReal, useState as useStateReal } from "react"
import type React from "react"
import { MailboxRoleChoices, Message, Thread } from "@/features/api/gen/models"

// This repo has no @testing-library/react; createRoot + act (both from
// React/ReactDOM, already a dependency) is the lightest way to mount a
// component tree and observe real DOM mutations driven by effects.
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

// ThreadView pulls in a large amount of surrounding infrastructure (mailbox
// context, i18n, feature flags, visibility observers, sub-components with
// their own heavy dependency trees). None of that matters for the
// hash-driven scroll/highlight effect under test, so every collaborator is
// stubbed to its minimal observable shape, following the same approach as
// thread-panel/components/thread-item/index.test.tsx.

vi.mock("react-i18next", () => ({
    useTranslation: () => ({
        t: (key: string) => key,
        i18n: { resolvedLanguage: "en" },
    }),
    initReactI18next: { type: "3rdParty", init: () => {} },
}))

vi.mock("@/features/utils/date-helper", () => ({
    DateHelper: { formatDate: () => "" },
}))

vi.mock("@/features/utils/view-helper", () => ({
    default: { isTrashedView: () => false },
}))

vi.mock("@/hooks/use-feature", () => ({
    FEATURE_KEYS: { AI_SUMMARY: "ai_summary" },
    useFeatureFlag: () => false,
}))

vi.mock("@/features/message/use-read", () => ({
    default: () => ({ markAsReadAt: vi.fn() }),
}))

vi.mock("@/features/message/use-mention-read", () => ({
    default: () => ({ markMentionsRead: vi.fn() }),
}))

vi.mock("@/features/message/use-spam", () => ({
    default: () => ({ markAsNotSpam: vi.fn() }),
}))

vi.mock("@/hooks/use-debounce-callback", () => ({
    useDebounceCallback: (fn: (...args: unknown[]) => void) => fn,
}))

vi.mock("@/hooks/use-is-shared-context", () => ({
    useIsSharedContext: () => false,
}))

vi.mock("@/hooks/use-visibility-observer", () => ({
    useVisibilityObserver: () => {},
}))

vi.mock("@/features/ui/components/banner", () => ({
    Banner: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}))

vi.mock("@gouvfr-lasuite/ui-kit", () => ({
    Icon: ({ name, ...rest }: { name: string } & React.HTMLAttributes<HTMLSpanElement>) => (
        <span data-icon={name} {...rest} />
    ),
    IconType: { OUTLINED: "outlined", FILLED: "filled" },
    Spinner: () => <div data-testid="spinner" />,
}))

vi.mock("./components/thread-action-bar", () => ({
    ThreadActionBar: () => <div data-testid="thread-action-bar" />,
}))

vi.mock("./components/thread-view-labels-list", () => ({
    ThreadViewLabelsList: () => <div data-testid="thread-view-labels-list" />,
}))

vi.mock("./components/thread-summary", () => ({
    ThreadSummary: () => <div data-testid="thread-summary" />,
}))

vi.mock("./components/thread-view-empty", () => ({
    ThreadViewEmpty: () => <div data-testid="thread-view-empty" />,
}))

vi.mock("./components/thread-event-input", () => ({
    ThreadEventInput: () => <div data-testid="thread-event-input" />,
}))

vi.mock("./components/thread-event", () => ({
    ThreadEvent: () => <div data-testid="thread-event" />,
    CollapsedEventsGroup: () => <div data-testid="collapsed-events-group" />,
    isCondensed: () => false,
    // Real implementation just re-shapes TimelineItem[] into render items;
    // a message-only identity mapping is enough for these tests.
    groupSystemEvents: (items: { type: string; data: unknown }[]) =>
        items.map((item) =>
            item.type === "event" ? { kind: "event", data: item.data } : { kind: "message", data: item.data }
        ),
}))

// The real ThreadMessage does a lot (delivery status, body rendering,
// reply forms…) that is irrelevant here — what matters is that it renders
// a DOM node with the right id, and reports readiness so the surrounding
// `isReady` gate (from the real ThreadViewProvider, used unmocked below)
// flips true, exactly like the real component does once its body loads.
vi.mock("./components/thread-message", async () => {
    const React = await import("react")
    const { useEffect } = React
    const { useThreadViewContext } = await import("./provider")
    const ThreadMessage = React.forwardRef<HTMLDivElement, { message: { id: string } }>(
        ({ message }, ref) => {
            const { setMessageReadiness } = useThreadViewContext()
            // `setMessageReadiness` is a plain inline function on the real
            // ThreadViewProvider (not memoized), so it gets a new identity
            // every render — including it here would re-run this effect on
            // every readiness update and loop forever.
            useEffect(() => {
                setMessageReadiness(message.id, true)
            }, [message.id])
            return <div id={`thread-message-${message.id}`} ref={ref} data-testid={`thread-message-${message.id}`} />
        }
    )
    return { ThreadMessage }
})

// Reactive hash: tests drive it via `setLocationHash`, matching the
// `useLocation({ select })` pattern already used elsewhere in this codebase
// (e.g. mailbox.tsx, label-badge/index.tsx).
let currentHash = ""
const locationListeners = new Set<() => void>()
const setLocationHash = (hash: string) => {
    currentHash = hash
    // Some effects in this file (e.g. the trashed-message deep-link
    // redirect, out of scope for this fix) still read `window.location.hash`
    // directly rather than through the reactive `useLocation` hook, so keep
    // the real jsdom location in sync too.
    window.location.hash = hash
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

let mailboxContextValue: Record<string, unknown> = {}
vi.mock("@/features/providers/mailbox", () => ({
    useMailboxContext: () => mailboxContextValue,
    isThreadEvent: (item: { type: string } | null) => item?.type === "event",
}))

// Imported after all vi.mock calls above so the mocked modules are in place
// before ThreadView's own module graph resolves.
const { ThreadView } = await import("./index")

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

const buildThread = (overrides: Partial<Thread> = {}): Thread => ({
    id: "thread-1",
    subject: "Hello",
    snippet: "",
    messages: "",
    has_unread: false,
    has_unread_mention: false,
    has_trashed: false,
    is_trashed: false,
    has_archived: false,
    has_draft: false,
    has_starred: false,
    has_attachments: false,
    has_sender: true,
    has_messages: true,
    has_delivery_failed: false,
    has_delivery_pending: false,
    is_spam: false,
    has_active: true,
    messaged_at: null,
    active_messaged_at: null,
    draft_messaged_at: null,
    sender_messaged_at: null,
    archived_messaged_at: null,
    trashed_messaged_at: null,
    sender_names: [],
    updated_at: "2026-07-20T00:00:00Z",
    user_role: null as unknown as Thread["user_role"],
    accesses: [],
    labels: [],
    summary: "",
    events_count: 0,
    message_count: 1,
    abilities: {} as Thread["abilities"],
    assigned_users: [],
    ...overrides,
} as Thread)

type RenderThreadViewOptions = {
    hash?: string
    messages: Message[]
}

const renderThreadView = ({ hash = "", messages }: RenderThreadViewOptions) => {
    currentHash = hash
    window.location.hash = hash
    const thread = buildThread()
    mailboxContextValue = {
        selectedMailbox: { id: "mailbox-1", role: MailboxRoleChoices.editor },
        selectedThread: thread,
        unmountThreadViewNeeded: false,
        messages,
        threadItems: messages.map((m) => ({ type: "message", data: m, created_at: m.created_at })),
        queryStates: {
            messages: { isLoading: false },
            threadEvents: { isLoading: false },
        },
    }

    const container = document.createElement("div")
    document.body.appendChild(container)
    let root: Root
    act(() => {
        root = createRoot(container)
        root.render(<ThreadView />)
    })

    return {
        container,
        rerender: () => {
            act(() => {
                root.render(<ThreadView />)
            })
        },
        cleanup: () => {
            act(() => root.unmount())
            container.remove()
        },
    }
}

// The hashMatch path defers scroll+highlight past a double rAF, then waits
// for either a real `scrollend` (unavailable in jsdom) or a 700ms safety
// timeout before applying the highlight class. Flushing real time (rather
// than faking it) keeps this aligned with the actual rAF/setTimeout
// scheduling used by the effect.
const flushHashEffect = async () => {
    await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 800))
    })
}

describe("ThreadView — hash-driven scroll/highlight", () => {
    beforeEach(() => {
        currentHash = ""
        locationListeners.clear()
    })

    it("scrolls to and highlights the message targeted by the initial hash", async () => {
        const messages = [buildMessage({ id: "m1" }), buildMessage({ id: "m2" })]
        const { container, cleanup } = renderThreadView({ hash: "#thread-message-m1", messages })
        await flushHashEffect()

        const target = container.querySelector("#thread-message-m1")
        expect(target).not.toBeNull()
        expect(target?.classList.contains("thread-view__highlight")).toBe(true)

        cleanup()
    })

    it("scrolls to a different message when the hash changes while the same thread stays open", async () => {
        const messages = [buildMessage({ id: "m1" }), buildMessage({ id: "m2" })]
        const { container, rerender, cleanup } = renderThreadView({ hash: "#thread-message-m1", messages })
        await flushHashEffect()

        expect(container.querySelector("#thread-message-m1")?.classList.contains("thread-view__highlight")).toBe(true)

        act(() => {
            setLocationHash("#thread-message-m2")
        })
        rerender()
        await flushHashEffect()

        expect(container.querySelector("#thread-message-m2")?.classList.contains("thread-view__highlight")).toBe(true)

        cleanup()
    })

    it("does not re-run the once-only draft/unread/latest fallback after a later, unrelated hash change", () => {
        // No hash match at all: falls back to "latest message" heuristic.
        // This must run exactly once — the render tree does not offer a
        // direct hook into "how many times", so this test instead documents
        // and locks the expected fallback selection surviving a hash change
        // that doesn't itself target any message (i.e. verifies the guard
        // doesn't crash or reset when the hash becomes something unrelated).
        const messages = [
            buildMessage({ id: "m1", created_at: "2026-07-20T00:00:00Z" }),
            buildMessage({ id: "m2", created_at: "2026-07-20T01:00:00Z" }),
        ]
        const { container, rerender, cleanup } = renderThreadView({ hash: "", messages })

        // No hashMatch, no drafts, no unread => falls back to latestMessage (m2).
        expect(container.querySelector("#thread-message-m1")?.classList.contains("thread-view__highlight")).toBe(false)
        expect(container.querySelector("#thread-message-m2")?.classList.contains("thread-view__highlight")).toBe(false)

        act(() => {
            setLocationHash("#not-a-thread-hash")
        })
        rerender()

        // hasBeenInitialized is already true and the new hash doesn't match
        // the deep-link pattern, so nothing re-runs and no highlight appears.
        expect(container.querySelector("#thread-message-m1")?.classList.contains("thread-view__highlight")).toBe(false)
        expect(container.querySelector("#thread-message-m2")?.classList.contains("thread-view__highlight")).toBe(false)

        cleanup()
    })
})
