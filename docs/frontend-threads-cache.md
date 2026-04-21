# Threads list cache — design & mutation contract

This document explains how the **frontend threads list cache** is managed,
why it pins certain threads, and what every mutation that touches threads
must do to keep the UI consistent.

It is required reading before adding or refactoring any hook that mutates
threads (read/unread, starred, archived, trashed, spam, draft delete/send,
labels, etc.).

The relevant code lives in:

- `src/frontend/src/features/providers/mailbox.tsx` — `MailboxProvider`
- `src/frontend/src/features/providers/mailbox-cache.ts` — pure cache helpers
- `src/frontend/src/features/message/use-*.tsx` — mutation hooks consuming the API


## 1. Server state vs. client state

There is **no global client state** for threads. The list comes from a
React Query infinite query (`useThreadsListInfinite`) keyed per mailbox and
per filter variant. The cache **is** the source of truth on the client; we
do not duplicate it into a Zustand/Redux store.

Two implications:

1. Every mutation reconciles by either **patching** the cache directly or
   **invalidating** it (which triggers a refetch).
2. The cache is keyed by URL search params (filter, search, label, etc.).
   The same thread can live in multiple cache variants simultaneously.

The query key is built by `getMailboxThreadsListQueryKey(mailboxId, searchParams)`:

```ts
['threads', <mailboxId>, 'list' | 'search', <normalized-other-params>]
```

`'list'` vs `'search'` lets us target the whole search subtree by prefix
without enumerating filter combinations.


## 2. Why pinning exists

Mutations like **mark-as-read** or **toggle-starred** flip a property that
the server uses to filter the list:

- Mark a thread as read while viewing the **"unread"** filter — the server
  will drop it on the next refetch.
- Unstar a thread while viewing the **"starred"** filter — same problem.

Without protection, the thread would disappear from under the user's cursor
the moment a refetch lands (polling, `invalidateQueries`, window focus).
That is jarring: the user wants to *see* the action they just performed.

**Pinning** is the protection mechanism:

1. The mutation calls `pinThreads(ids, patcher)`. This:
   - patches the thread(s) in every cached list variant via
     `patchThreadsInCache` (so the UI reflects the new state immediately);
   - records the thread ids in `pinnedThreadIdsRef` (a `useRef<Set<string>>`).
2. The infinite query's `structuralSharing` callback runs `mergePinnedThreads`
   on every refetch. Threads that are pinned but missing from the new server
   data are **re-injected** at the index they previously occupied within
   their original page.
3. When the server returns a pinned thread on its own, the pin is **inert**:
   `mergePinnedThreads` only re-injects threads missing from the response,
   so the server's data wins by default. The pin entry stays in the set but
   has no effect until the thread disappears again.
4. Pinned ids are cleared in bulk when the user changes mailbox or filter
   (a `useEffect` watches `selectedMailbox.id` and `searchParams`).


### Pinning rules

- Pin **only** when the mutation flips a property the server filters on AND
  the user should still see the thread under the current view.
- Always pair the pin with a cache patch — pinning a stale thread is worse
  than dropping it, because it shows wrong data.
- Re-insertion preserves **per-page semantics**. Pinned threads never move
  to page 0 if they originally lived on page 1; otherwise flattening the
  pages would yield duplicates.


## 3. Why **unpinning** matters

A pin survives until `unpinThreads` is called or the user changes filter /
mailbox. When a mutation **moves the thread out of the active view**
(archive, spam, trash, draft delete, draft send), the server will *never*
return it under that filter again — so the pin would persist and
`mergePinnedThreads` would re-inject the thread on every subsequent
refetch. The user sees a "ghost" thread that ignores their action.

Every mutation that intentionally removes a thread from the current view
**must call** `unpinThreads(ids)` before invalidating the list.

```ts
const { unpinThreads, invalidateMailbox } = useMailboxContext();

onSuccess: (data) => {
    unpinThreads(data.thread_ids ?? []);
    invalidateMailbox();
}
```

Order matters less than presence: as long as the pin is gone before the
refetch settles, `structuralSharing` will let the thread disappear.


## 4. Mutation playbook

Every thread mutation falls into one of three categories. Pick the right
playbook and stick to it.

### A. "Stay visible" mutations (read/unread, starred/unstarred)

These flip a server-filterable property but the thread should remain
visible in the current view until the user navigates away.

```ts
onSuccess: (data) => {
    pinThreads(data.thread_ids ?? [], (thread) => ({
        ...thread,
        // recompute server-derived flags so the cached row matches reality
        has_unread: deriveThreadHasUnread(thread.messaged_at, data.read_at ?? null),
        accesses: thread.accesses.map((access) => ...),
    }));
    invalidateThreadsStats();
}
```

The patcher encodes the **domain semantics** (recomputing `has_unread`,
flipping `has_starred`, mutating `accesses`, etc.) — `mailbox-cache.ts`
does not know about them.

### B. "Leave the view" mutations (archive, spam, trash, draft delete/send)

These remove the thread from the active filter. We do not patch — we
unpin and let the next refetch reconcile.

```ts
onSuccess: (data) => {
    unpinThreads(data.thread_ids ?? []);
    invalidateMailbox();
    invalidateThreadsStats();
}
```

### C. Per-thread message mutations (draft body, message read, etc.)

Patch the **per-thread messages cache** (`['messages', threadId]`) via
`patchMessagesInCache` / `removeMessagesFromCache`. These do not touch the
threads list. Invalidate `invalidateThreadMessages()` if a refetch is
warranted.


## 5. Invalidation helpers exposed by `MailboxProvider`

| Helper                       | When to call it                                                      |
|------------------------------|----------------------------------------------------------------------|
| `pinThreads(ids, patcher)`   | "Stay visible" mutations (category A)                                |
| `unpinThreads(ids)`          | "Leave the view" mutations (category B), before invalidating         |
| `patchMessages(threadId, p)` | Per-thread message mutations (category C)                            |
| `removeMessages(...)`        | Drop messages from a thread's cache (e.g. draft deletion)            |
| `invalidateThreadList()`     | Force refetch of every list variant of the current mailbox            |
| `invalidateThreadMessages()` | Force refetch of the selected thread's messages                       |
| `invalidateMailbox()`        | Shorthand for both above                                              |
| `invalidateThreadEvents()`   | Refetch events of the selected thread                                 |
| `invalidateThreadsStats()`   | Refetch sidebar counters (excludes per-label stats)                   |
| `invalidateLabels()`         | Refetch the labels list                                               |


## 6. Decision flow when adding a new mutation

1. **Does the mutation flip a property the server filters on?**
   - No → category C (messages-only) or just invalidate.
   - Yes → step 2.
2. **Should the thread remain in the current view after the mutation?**
   - Yes → category A: `pinThreads(ids, patcher)` + invalidate stats only.
   - No → category B: `unpinThreads(ids)` + `invalidateMailbox()`.
3. **Are there per-thread side effects (messages, events)?**
   - Yes → also patch / invalidate the relevant per-thread caches.

When in doubt, write a unit test against `mergePinnedThreads` reproducing
the user-visible scenario before changing behaviour.
