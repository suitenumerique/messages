import { describe, it, expect } from 'vitest';
import type { TFunction } from 'i18next';
import { AssignmentEvent, buildAssignmentMessage } from './assignment-message';
import { ThreadEvent, ThreadEventTypeEnum } from '@/features/api/gen/models';

const SELF_ID = 'user-self';

// Minimal i18n stub: returns the key with {{x}} placeholders replaced. Good
// enough to assert which branch was picked and what parameters it received.
const fakeT: TFunction = ((key: string, params?: Record<string, unknown>) => {
    if (!params) return key;
    return Object.entries(params).reduce(
        (acc, [k, v]) => acc.replace(new RegExp(`\\{\\{${k}\\}\\}`, 'g'), String(v)),
        key,
    );
}) as unknown as TFunction;

const makeEvent = (
  type: 'assign' | 'unassign',
  authorId: string | null,
  assignees: { id: string; name: string }[],
  // Override author_display independently of authorId so tests can build the
  // webhook/channel actor shape (author === null but author_display set), which
  // buildAssignmentMessage special-cases as a named third party.
  authorDisplay?: string | null,
): AssignmentEvent => ({
  id: 'e',
  thread: 't',
  type,
  channel: null,
  author: authorId === null
    ? (null as unknown as ThreadEvent['author'])
    : ({ id: authorId, full_name: `User ${authorId}`, email: `${authorId}@ex.com` } as ThreadEvent['author']),
  author_display:
    authorDisplay !== undefined
      ? authorDisplay
      : authorId === null ? null : `User ${authorId}`,
  data: { assignees },
  has_unread_mention: false,
  is_editable: false,
  created_at: '2026-01-01T10:00:00Z',
  updated_at: '2026-01-01T10:00:00Z',
});

describe('buildAssignmentMessage', () => {
    it('A — self assigned themself', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, SELF_ID, [{ id: SELF_ID, name: 'Me' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('You assigned yourself');
    });

    it('B — self assigned others + themself', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, SELF_ID, [
            { id: SELF_ID, name: 'Me' },
            { id: 'bob', name: 'Bob' },
        ]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('You assigned Bob and yourself');
    });

    it('C — self assigned others only', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, SELF_ID, [{ id: 'bob', name: 'Bob' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('You assigned Bob');
    });

    it('D — other assigned the current user', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, 'alice', [{ id: SELF_ID, name: 'Me' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice assigned you');
    });

    it('E — other assigned the current user + others', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, 'alice', [
            { id: SELF_ID, name: 'Me' },
            { id: 'bob', name: 'Bob' },
        ]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice assigned you and Bob');
    });

    it('F — third party self-assigned (viewer not involved)', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, 'alice', [{ id: 'alice', name: 'Alice' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice assigned themself');
    });

    it('G — third party self-assigned + others (viewer not involved)', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, 'alice', [
            { id: 'alice', name: 'Alice' },
            { id: 'bob', name: 'Bob' },
        ]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice assigned themself and Bob');
    });

    it('H — third party assigned viewer and themself', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, 'alice', [
            { id: SELF_ID, name: 'Me' },
            { id: 'alice', name: 'Alice' },
        ]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice assigned you and themself');
    });

    it('I — third party assigned viewer, themself and others', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, 'alice', [
            { id: SELF_ID, name: 'Me' },
            { id: 'alice', name: 'Alice' },
            { id: 'bob', name: 'Bob' },
        ]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice assigned you, themself and Bob');
    });

    it('J — other assigned others (no self or author-self involved)', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, 'alice', [{ id: 'bob', name: 'Bob' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice assigned Bob');
    });

    it('third party self-unassigned', () => {
        const event = makeEvent(ThreadEventTypeEnum.unassign, 'alice', [{ id: 'alice', name: 'Alice' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice unassigned themself');
    });

    it('webhook actor (author null, author_display set) assigns others', () => {
        const event = makeEvent(
            ThreadEventTypeEnum.assign,
            null,
            [{ id: 'bob', name: 'Bob' }],
            'Webhook: CRM',
        );
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('Webhook: CRM assigned Bob');
    });

    it('webhook actor (author null, author_display set) assigns the current user', () => {
        const event = makeEvent(
            ThreadEventTypeEnum.assign,
            null,
            [{ id: SELF_ID, name: 'Me' }],
            'Webhook: CRM',
        );
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('Webhook: CRM assigned you');
    });

    it('K — system unassigned the current user', () => {
        const event = makeEvent(ThreadEventTypeEnum.unassign, null, [{ id: SELF_ID, name: 'Me' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('You were unassigned');
    });

    it('K2 — system unassigned the current user along with others', () => {
        const event = makeEvent(ThreadEventTypeEnum.unassign, null, [
            { id: SELF_ID, name: 'Me' },
            { id: 'bob', name: 'Bob' },
            { id: 'charlie', name: 'Charlie' },
        ]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('You and Bob, Charlie were unassigned');
    });

    it('L — system unassigned someone else', () => {
        const event = makeEvent(ThreadEventTypeEnum.unassign, null, [{ id: 'bob', name: 'Bob' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('Bob was unassigned');
    });

    it('L — system unassigned several other users', () => {
        const event = makeEvent(ThreadEventTypeEnum.unassign, null, [
            { id: 'bob', name: 'Bob' },
            { id: 'charlie', name: 'Charlie' },
        ]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('Bob, Charlie was unassigned');
    });

    it('self unassigned themself', () => {
        const event = makeEvent(ThreadEventTypeEnum.unassign, SELF_ID, [{ id: SELF_ID, name: 'Me' }]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('You unassigned yourself');
    });

    it('other unassigned the current user + others', () => {
        const event = makeEvent(ThreadEventTypeEnum.unassign, 'alice', [
            { id: SELF_ID, name: 'Me' },
            { id: 'bob', name: 'Bob' },
        ]);
        expect(buildAssignmentMessage(event, SELF_ID, fakeT)).toBe('User alice unassigned you and Bob');
    });

    it('falls back to 3rd-person wording when no currentUserId is provided', () => {
        const event = makeEvent(ThreadEventTypeEnum.assign, 'alice', [{ id: SELF_ID, name: 'Me' }]);
        expect(buildAssignmentMessage(event, undefined, fakeT)).toBe('User alice assigned Me');
    });
});
