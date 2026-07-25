import { beforeEach, describe, expect, it, vi } from "vitest"
import { createRoot } from "react-dom/client"
import { act } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fetchAPI } from "@/features/api/fetch-api"
import { useMessageSummaries } from "./use-message-summaries"

// This repo has no @testing-library/react (confirmed absent from
// package.json/node_modules), so there is no renderHook/waitFor. Following
// the same pattern as thread-item/index.test.tsx: mount a tiny host
// component with createRoot + act and read the hook's state back off the
// DOM, polling with act(async) + a short delay instead of waitFor.
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// vi.hoisted runs before every import in this file (unlike a plain
// top-level statement, which would run after use-message-summaries' own
// imports — including features/api/fetch-api -> features/api/api-error ->
// features/i18n/initI18n — have already executed and crashed on a missing
// localStorage). Node's global localStorage is experimental and not
// enabled in this project's vitest run, so polyfill it (same fix as
// thread-item/index.test.tsx).
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

vi.mock("@/features/api/fetch-api")

const Host = ({
    threadId,
    mailboxId,
    enabled,
}: {
    threadId: string
    mailboxId: string
    enabled: boolean
}) => {
    const query = useMessageSummaries(threadId, mailboxId, { enabled })
    return (
        <div
            data-testid="host"
            data-status={query.status}
            data-snippet={query.data?.[0]?.snippet ?? ""}
        />
    )
}

const renderHost = (props: { threadId: string; mailboxId: string; enabled: boolean }) => {
    const container = document.createElement("div")
    document.body.appendChild(container)
    const root = createRoot(container)
    const queryClient = new QueryClient()

    act(() => {
        root.render(
            <QueryClientProvider client={queryClient}>
                <Host {...props} />
            </QueryClientProvider>
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

const waitUntil = async (check: () => boolean, timeoutMs = 2000) => {
    const start = Date.now()
    while (!check()) {
        if (Date.now() - start > timeoutMs) {
            throw new Error("waitUntil: condition not met within timeout")
        }
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 5))
        })
    }
}

describe("useMessageSummaries", () => {
    // vitest doesn't clear mocks between tests by default here (no
    // clearMocks in vitest.config.ts), so the first test's call count would
    // otherwise leak into the second.
    beforeEach(() => {
        vi.clearAllMocks()
    })

    it("fetches summaries for the given thread with summary=true and mailbox_id", async () => {
        const mocked = vi.mocked(fetchAPI)
        mocked.mockResolvedValue({
            data: [
                {
                    id: "m1",
                    sender: { id: "s1", name: "Alice", email: "a@example.com" },
                    sent_at: "2026-07-20T10:00:00Z",
                    is_unread: false,
                    is_draft: false,
                    has_attachments: false,
                    snippet: "Hi",
                },
            ],
            status: 200,
        } as never)

        const { container, cleanup } = renderHost({
            threadId: "thread-1",
            mailboxId: "mb-1",
            enabled: true,
        })

        await waitUntil(() => container.querySelector('[data-testid="host"]')?.getAttribute("data-status") === "success")

        const host = container.querySelector('[data-testid="host"]')
        expect(host?.getAttribute("data-snippet")).toBe("Hi")
        expect(mocked).toHaveBeenCalledWith(
            expect.stringContaining("/messages/"),
            expect.objectContaining({
                params: { thread_id: "thread-1", mailbox_id: "mb-1", summary: "true" },
            })
        )

        cleanup()
    })

    it("does not fetch when enabled is false", () => {
        const mocked = vi.mocked(fetchAPI)
        const { cleanup } = renderHost({ threadId: "thread-1", mailboxId: "mb-1", enabled: false })

        expect(mocked).not.toHaveBeenCalled()

        cleanup()
    })
})
