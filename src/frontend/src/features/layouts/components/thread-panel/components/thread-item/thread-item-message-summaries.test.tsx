import { describe, expect, it, vi } from "vitest"
import { createRoot } from "react-dom/client"
import { act } from "react"
import type React from "react"
import { ThreadItemMessageSummaries } from "./thread-item-message-summaries"
import { useMessageSummaries } from "./use-message-summaries"
import type { MessageSummary } from "./types"

// This repo has no @testing-library/react; createRoot + act (both from
// React/ReactDOM, already a dependency) is the lightest way to mount a
// component and dispatch a real click without adding one. Same pattern as
// thread-item/index.test.tsx and use-message-summaries.test.tsx.
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// vi.hoisted runs before every import in this file (unlike a plain
// top-level statement, which would run after this file's own imports —
// including features/i18n/initI18n, reached transitively — have already
// executed and crashed on a missing localStorage). Node's global
// localStorage is experimental and not enabled in this project's vitest
// run, so polyfill it (same fix as the sibling test files).
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
    // features/i18n/initI18n calls i18n.use(initReactI18next) at import
    // time (transitively) — it just needs to exist as a valid i18next
    // plugin shape, its behaviour is irrelevant to this test.
    initReactI18next: { type: "3rdParty", init: () => {} },
}))

vi.mock("@/features/utils/date-helper", () => ({
    DateHelper: { formatDate: (date: string) => date },
}))

vi.mock("@tanstack/react-router", () => ({
    Link: ({ children, ...rest }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
        <a {...rest}>{children}</a>
    ),
}))

vi.mock("@gouvfr-lasuite/cunningham-react", () => ({
    Button: ({ children, ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
        <button type="button" {...rest}>
            {children}
        </button>
    ),
}))

vi.mock("@gouvfr-lasuite/ui-kit", () => ({
    Icon: ({ name, ...rest }: { name: string } & React.HTMLAttributes<HTMLSpanElement>) => (
        <span data-icon={name} {...rest} />
    ),
    IconType: { OUTLINED: "outlined", FILLED: "filled" },
    Spinner: (props: React.HTMLAttributes<HTMLSpanElement>) => <span data-testid="spinner" {...props} />,
}))

vi.mock("./use-message-summaries")

const mockUseMessageSummaries = (overrides: {
    data?: MessageSummary[]
    isLoading?: boolean
    isError?: boolean
    refetch?: () => void
}) => {
    vi.mocked(useMessageSummaries).mockReturnValue({
        data: overrides.data,
        isLoading: overrides.isLoading ?? false,
        isError: overrides.isError ?? false,
        refetch: overrides.refetch ?? vi.fn(),
    } as unknown as ReturnType<typeof useMessageSummaries>)
}

const renderSummaries = () => {
    const container = document.createElement("div")
    document.body.appendChild(container)
    const root = createRoot(container)

    act(() => {
        root.render(<ThreadItemMessageSummaries threadId="t1" mailboxId="mb1" />)
    })

    return {
        container,
        cleanup: () => {
            act(() => root.unmount())
            container.remove()
        },
    }
}

describe("ThreadItemMessageSummaries", () => {
    it("shows a loading state while summaries are fetching", () => {
        mockUseMessageSummaries({ isLoading: true, data: undefined })
        const { container, cleanup } = renderSummaries()

        expect(container.querySelector('[role="status"]')).not.toBeNull()

        cleanup()
    })

    it("shows an inline error with a retry button on failure", () => {
        const refetch = vi.fn()
        mockUseMessageSummaries({ isError: true, refetch })
        const { container, cleanup } = renderSummaries()

        const button = container.querySelector("button") as HTMLButtonElement
        expect(button).not.toBeNull()
        act(() => {
            button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }))
        })

        expect(refetch).toHaveBeenCalledTimes(1)

        cleanup()
    })

    it("renders one row per message with sender, date and snippet", () => {
        mockUseMessageSummaries({
            data: [
                {
                    id: "m1",
                    sender: { id: "s1", name: "Alice", email: "a@x.com" },
                    sent_at: "2026-07-20T10:00:00Z",
                    is_unread: true,
                    is_draft: false,
                    has_attachments: false,
                    snippet: "Hello there",
                },
            ],
        })
        const { container, cleanup } = renderSummaries()

        expect(container.textContent).toContain("Alice")
        expect(container.textContent).toContain("Hello there")

        cleanup()
    })

    it("visually distinguishes a draft summary row", () => {
        mockUseMessageSummaries({
            data: [
                {
                    id: "m1",
                    sender: { id: "s1", name: "Alice", email: "a@x.com" },
                    sent_at: null,
                    is_unread: false,
                    is_draft: true,
                    has_attachments: false,
                    snippet: "",
                },
            ],
        })
        const { container, cleanup } = renderSummaries()

        expect(container.textContent).toContain("Draft")
        expect(container.querySelector(".thread-item__summary-link--draft")).not.toBeNull()

        cleanup()
    })
})
