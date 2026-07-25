import { describe, expect, it, vi } from "vitest"
import { createRoot } from "react-dom/client"
import { act } from "react"
import type React from "react"
import { ThreadItem } from "./index"
import type { Thread } from "@/features/api/gen/models"

// This repo has no @testing-library/react; createRoot + act (both from
// React/ReactDOM, already a dependency) is the lightest way to mount a
// component and dispatch a real click without adding one.
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// vi.hoisted runs before every import in this file (unlike a plain
// top-level statement, which would run after ThreadItem's own imports —
// including features/i18n/initI18n, reached transitively via
// features/api/api-error.ts — have already executed and crashed on a
// missing localStorage). Node's global localStorage is experimental and
// not enabled in this project's vitest run, so polyfill it.
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

// This component pulls in the router, i18n, drag context and several
// presentational children — none of which matter for the expand/collapse
// behaviour under test. Mocked the same way the repo's other component
// tests stub out leaf dependencies (see assignees-avatar-group/index.test.tsx),
// but here the component genuinely needs to mount/click (not just render to
// a string), so we drive it with createRoot + act instead of
// renderToStaticMarkup — there is no @testing-library/react in this repo.
vi.mock("react-i18next", () => ({
    useTranslation: () => ({
        t: (key: string) => key,
        i18n: { resolvedLanguage: "en" },
    }),
    // features/i18n/initI18n calls i18n.use(initReactI18next) at import
    // time (transitively, via api-error.ts et al.) — it just needs to
    // exist as a valid i18next plugin shape, its behaviour is irrelevant
    // to this test.
    initReactI18next: { type: "3rdParty", init: () => {} },
}))

// The real date-helper transitively imports features/i18n/initI18n, which
// reaches for localStorage at module load time. Its formatting logic is
// irrelevant to the chevron behaviour under test, so stub it out entirely.
vi.mock("@/features/utils/date-helper", () => ({
    DateHelper: { formatDate: () => "" },
}))

vi.mock("@tanstack/react-router", () => ({
    useParams: () => ({}),
    Link: ({ children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
        <a {...rest}>{children}</a>
    ),
}))

vi.mock("@/features/layouts/components/layout-context", () => ({
    useLayoutDragContext: () => ({
        setIsDragging: vi.fn(),
        setDragAction: vi.fn(),
    }),
}))

vi.mock("@/features/message/use-can-edit-threads", () => ({
    default: () => true,
}))

vi.mock("@gouvfr-lasuite/cunningham-react", () => ({
    Checkbox: (props: React.InputHTMLAttributes<HTMLInputElement>) => (
        <input type="checkbox" {...props} />
    ),
    Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock("@gouvfr-lasuite/ui-kit", () => ({
    Icon: ({ name, ...rest }: { name: string } & React.HTMLAttributes<HTMLSpanElement>) => (
        <span data-icon={name} {...rest} />
    ),
    IconSize: { SMALL: "small" },
    IconType: { OUTLINED: "outlined", FILLED: "filled" },
}))

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

type RenderOverrides = {
    thread: Thread
    isExpanded: boolean
    onToggleExpand: () => void
}

const renderThreadItem = ({ thread, isExpanded, onToggleExpand }: RenderOverrides) => {
    const container = document.createElement("div")
    document.body.appendChild(container)
    const root = createRoot(container)

    act(() => {
        root.render(
            <ThreadItem
                thread={thread}
                isSelected={false}
                onToggle={vi.fn()}
                onSelectRange={vi.fn()}
                selectedThreadIds={new Set()}
                isSelectionMode={false}
                isExpanded={isExpanded}
                onToggleExpand={onToggleExpand}
                tabIndex={0}
                itemRef={() => {}}
                onFocusItem={vi.fn()}
            />
        )
    })

    return {
        container,
        cleanup: () => {
            act(() => root.unmount())
            container.remove()
        },
    }
}

describe("ThreadItem — expand chevron", () => {
    it("renders no chevron when message_count is 1 or less", () => {
        const thread = buildThread({ message_count: 1 })
        const { container, cleanup } = renderThreadItem({ thread, isExpanded: false, onToggleExpand: vi.fn() })

        expect(container.querySelector("button")).toBeNull()

        cleanup()
    })

    it("renders a chevron when message_count is greater than 1", () => {
        const thread = buildThread({ message_count: 3 })
        const { container, cleanup } = renderThreadItem({ thread, isExpanded: false, onToggleExpand: vi.fn() })

        const button = container.querySelector("button")
        expect(button).not.toBeNull()
        expect(button?.getAttribute("aria-expanded")).toBe("false")

        cleanup()
    })

    it("clicking the chevron calls onToggleExpand without navigating", () => {
        const thread = buildThread({ message_count: 3 })
        const onToggleExpand = vi.fn()
        const { container, cleanup } = renderThreadItem({ thread, isExpanded: false, onToggleExpand })

        const button = container.querySelector("button") as HTMLButtonElement
        act(() => {
            button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }))
        })

        expect(onToggleExpand).toHaveBeenCalledTimes(1)

        cleanup()
    })

    it("renders the chevron and the thread link as siblings, not nested", () => {
        const thread = buildThread({ message_count: 3 })
        const { container, cleanup } = renderThreadItem({ thread, isExpanded: false, onToggleExpand: vi.fn() })

        const button = container.querySelector("button")
        const link = container.querySelector("a")
        expect(button).not.toBeNull()
        expect(link).not.toBeNull()
        // Interactive content cannot be nested inside an <a>: the chevron
        // must not be a descendant of the Link, and vice versa.
        expect(link?.contains(button)).toBe(false)
        expect(button?.contains(link)).toBe(false)

        cleanup()
    })
})
