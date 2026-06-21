import { describe, it, expect } from 'vitest';
import { groupSystemEvents, RenderItem } from './index';
import { ThreadEvent, ThreadEventTypeEnum, Message } from '@/features/api/gen/models';
import { TimelineItem } from '@/features/providers/mailbox';

const makeAssignEvent = (
    id: string,
    authorId: string | null,
    assignees: { id: string; name: string }[],
    type: ThreadEventTypeEnum = ThreadEventTypeEnum.assign,
    createdAt = '2026-01-01T10:00:00Z',
): ThreadEvent => ({
    id,
    thread: 'thread-1',
    type,
    channel: null,
    author: authorId
        ? ({ id: authorId, full_name: `User ${authorId}`, email: `${authorId}@example.com` } as ThreadEvent['author'])
        : (null as unknown as ThreadEvent['author']),
    author_display: authorId ? `User ${authorId}` : null,
    data: { assignees },
    has_unread_mention: false,
    is_editable: false,
    created_at: createdAt,
    updated_at: createdAt,
});

const makeIMEvent = (id: string, authorId: string, createdAt = '2026-01-01T10:00:15Z'): ThreadEvent => ({
    id,
    thread: 'thread-1',
    type: ThreadEventTypeEnum.im,
    channel: null,
    author: { id: authorId, full_name: `User ${authorId}`, email: `${authorId}@example.com` } as ThreadEvent['author'],
    author_display: `User ${authorId}`,
    data: { content: 'hello', mentions: [] },
    has_unread_mention: false,
    is_editable: false,
    created_at: createdAt,
    updated_at: createdAt,
});

const makeMessageItem = (id: string): TimelineItem => ({
    type: 'message',
    data: { id } as unknown as Message,
    created_at: '2026-01-01T10:00:30Z',
});

const asEventItem = (event: ThreadEvent): TimelineItem => ({
    type: 'event',
    data: event,
    created_at: event.created_at,
});

describe('groupSystemEvents', () => {
    it('leaves messages untouched and wraps a lone event individually', () => {
        const assign = makeAssignEvent('e1', 'alice', [{ id: 'bob', name: 'Bob' }]);
        const items: TimelineItem[] = [makeMessageItem('m1'), asEventItem(assign)];

        const result = groupSystemEvents(items);

        expect(result).toHaveLength(2);
        expect(result[0].kind).toBe('message');
        expect(result[1]).toEqual<RenderItem>({
            kind: 'event',
            data: assign,
            created_at: assign.created_at,
        });
    });

    it('keeps a run of 2 non-IM events inline (below collapse threshold)', () => {
        const e1 = makeAssignEvent('e1', 'alice', [{ id: 'bob', name: 'Bob' }]);
        const e2 = makeAssignEvent('e2', 'alice', [{ id: 'bob', name: 'Bob' }], ThreadEventTypeEnum.unassign);
        const items: TimelineItem[] = [asEventItem(e1), asEventItem(e2)];

        const result = groupSystemEvents(items);

        expect(result).toHaveLength(2);
        expect(result.every((r) => r.kind === 'event')).toBe(true);
    });

    it('collapses a run of 3 non-IM events regardless of author', () => {
        const e1 = makeAssignEvent('e1', 'alice', [{ id: 'bob', name: 'Bob' }]);
        const e2 = makeAssignEvent('e2', 'charlie', [{ id: 'dave', name: 'Dave' }]);
        const e3 = makeAssignEvent('e3', 'alice', [{ id: 'bob', name: 'Bob' }], ThreadEventTypeEnum.unassign);
        const items: TimelineItem[] = [asEventItem(e1), asEventItem(e2), asEventItem(e3)];

        const result = groupSystemEvents(items);

        expect(result).toHaveLength(1);
        if (result[0].kind !== 'collapsed_events') throw new Error('type-guard');
        expect(result[0].events).toEqual([e1, e2, e3]);
        expect(result[0].created_at).toBe(e3.created_at);
    });

    it('breaks a long run when a message sits in the middle', () => {
        const e1 = makeAssignEvent('e1', 'alice', [{ id: 'bob', name: 'Bob' }]);
        const e2 = makeAssignEvent('e2', 'alice', [{ id: 'bob', name: 'Bob' }], ThreadEventTypeEnum.unassign);
        const e3 = makeAssignEvent('e3', 'alice', [{ id: 'charlie', name: 'Charlie' }]);
        const items: TimelineItem[] = [
            asEventItem(e1),
            asEventItem(e2),
            makeMessageItem('m1'),
            asEventItem(e3),
        ];

        const result = groupSystemEvents(items);

        expect(result.map((r) => r.kind)).toEqual(['event', 'event', 'message', 'event']);
    });

    it('breaks a long run when an IM sits in the middle', () => {
        const e1 = makeAssignEvent('e1', 'alice', [{ id: 'bob', name: 'Bob' }]);
        const e2 = makeAssignEvent('e2', 'alice', [{ id: 'bob', name: 'Bob' }], ThreadEventTypeEnum.unassign);
        const im = makeIMEvent('im1', 'alice');
        const e3 = makeAssignEvent('e3', 'alice', [{ id: 'charlie', name: 'Charlie' }]);
        const e4 = makeAssignEvent('e4', 'alice', [{ id: 'charlie', name: 'Charlie' }], ThreadEventTypeEnum.unassign);
        const items: TimelineItem[] = [
            asEventItem(e1),
            asEventItem(e2),
            asEventItem(im),
            asEventItem(e3),
            asEventItem(e4),
        ];

        const result = groupSystemEvents(items);

        expect(result.map((r) => r.kind)).toEqual(['event', 'event', 'event', 'event', 'event']);
    });

    it('breaks a long run when a non-assignment system event sits in the middle', () => {
        // Guards against future ThreadEventType values (e.g. label/state changes)
        // being silently absorbed by the buffer: CollapsedEventsGroup only knows
        // how to render assign/unassign, so non-assignment events must flush.
        const e1 = makeAssignEvent('e1', 'alice', [{ id: 'bob', name: 'Bob' }]);
        const e2 = makeAssignEvent('e2', 'alice', [{ id: 'bob', name: 'Bob' }], ThreadEventTypeEnum.unassign);
        const unknown: ThreadEvent = {
            ...makeAssignEvent('x1', 'alice', []),
            type: 'state_changed' as ThreadEventTypeEnum,
        };
        const e3 = makeAssignEvent('e3', 'alice', [{ id: 'charlie', name: 'Charlie' }]);
        const items: TimelineItem[] = [
            asEventItem(e1),
            asEventItem(e2),
            asEventItem(unknown),
            asEventItem(e3),
        ];

        const result = groupSystemEvents(items);

        expect(result.map((r) => r.kind)).toEqual(['event', 'event', 'event', 'event']);
    });

    it('collapses only the sub-run that reaches the threshold', () => {
        const a1 = makeAssignEvent('e1', 'alice', [{ id: 'bob', name: 'Bob' }]);
        const a2 = makeAssignEvent('e2', 'alice', [{ id: 'bob', name: 'Bob' }], ThreadEventTypeEnum.unassign);
        const im = makeIMEvent('im1', 'alice');
        const b1 = makeAssignEvent('e3', 'alice', [{ id: 'charlie', name: 'Charlie' }]);
        const b2 = makeAssignEvent('e4', 'alice', [{ id: 'charlie', name: 'Charlie' }], ThreadEventTypeEnum.unassign);
        const b3 = makeAssignEvent('e5', 'alice', [{ id: 'dave', name: 'Dave' }]);
        const items: TimelineItem[] = [
            asEventItem(a1),
            asEventItem(a2),
            asEventItem(im),
            asEventItem(b1),
            asEventItem(b2),
            asEventItem(b3),
        ];

        const result = groupSystemEvents(items);

        expect(result.map((r) => r.kind)).toEqual([
            'event',
            'event',
            'event',
            'collapsed_events',
        ]);
    });
});
