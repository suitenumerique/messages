import { describe, expect, it } from 'vitest';
import type { ChangeFlagRequestRequest } from '@/features/api/gen/models';
import ToastBatchHelper from './index';

const flagThreads = (threadIds: string[], mailboxId = 'mailbox'): ChangeFlagRequestRequest => ({
    flag: 'starred',
    value: true,
    thread_ids: threadIds,
    mailbox_id: mailboxId,
});

const flagMessages = (messageIds: string[]): ChangeFlagRequestRequest => ({
    flag: 'trashed',
    value: true,
    message_ids: messageIds,
});

describe('ToastBatchHelper', () => {
    describe('getKey', () => {
        it('should share a key between flags of the same flag, scope and mailbox', () => {
            expect(ToastBatchHelper.getKey(flagThreads(['t1']))).toBe(ToastBatchHelper.getKey(flagThreads(['t2'])));
        });

        it('should separate threads from messages', () => {
            const threadsKey = ToastBatchHelper.getKey({ ...flagMessages(['m1']), thread_ids: ['t1'] });
            expect(threadsKey).not.toBe(ToastBatchHelper.getKey(flagMessages(['m1'])));
        });

        it('should separate the messages of different threads', () => {
            expect(ToastBatchHelper.getKey(flagMessages(['m1']), 'a')).not.toBe(ToastBatchHelper.getKey(flagMessages(['m2']), 'b'));
        });

        it('should share a key between the messages of the same thread', () => {
            expect(ToastBatchHelper.getKey(flagMessages(['m1']), 'a')).toBe(ToastBatchHelper.getKey(flagMessages(['m2']), 'a'));
        });

        it('should not separate threads by the thread of messages', () => {
            expect(ToastBatchHelper.getKey(flagThreads(['t1']), 'a')).toBe(ToastBatchHelper.getKey(flagThreads(['t2']), 'b'));
        });

        it('should separate mailboxes', () => {
            expect(ToastBatchHelper.getKey(flagThreads(['t1'], 'a'))).not.toBe(ToastBatchHelper.getKey(flagThreads(['t1'], 'b')));
        });
    });

    describe('merge', () => {
        it('should start a batch from the first flag', () => {
            expect(ToastBatchHelper.merge(undefined, 'toast', flagThreads(['t1', 't2']), 1)).toEqual({
                toastId: 'toast',
                threadIds: ['t1', 't2'],
                messageIds: [],
                skippedCount: 1,
            });
        });

        it('should add up the items and the skipped counts of successive flags', () => {
            const first = ToastBatchHelper.merge(undefined, 'toast', flagThreads(['t1', 't2']), 1);
            const merged = ToastBatchHelper.merge(first, 'toast', flagThreads(['t3']), 0);

            expect(merged.threadIds).toEqual(['t1', 't2', 't3']);
            expect(merged.skippedCount).toBe(2);
        });

        it('should not count twice a thread flagged twice', () => {
            const first = ToastBatchHelper.merge(undefined, 'toast', flagThreads(['t1']), 1);
            const merged = ToastBatchHelper.merge(first, 'toast', flagThreads(['t1']), 1);

            expect(merged.threadIds).toEqual(['t1']);
            expect(merged.skippedCount).toBe(0);
        });

        it('should keep the skipped count unknown once a response lacks a count', () => {
            const first = ToastBatchHelper.merge(undefined, 'toast', flagThreads(['t1']), 1);

            expect(ToastBatchHelper.merge(first, 'toast', flagThreads(['t2']), undefined).skippedCount).toBeUndefined();
        });

        it('should count every message as skipped when none of their threads was updated', () => {
            expect(ToastBatchHelper.merge(undefined, 'toast', flagMessages(['m1', 'm2']), 0).skippedCount).toBe(2);
        });

        it('should count no message as skipped when one of their threads was updated', () => {
            expect(ToastBatchHelper.merge(undefined, 'toast', flagMessages(['m1', 'm2']), 1).skippedCount).toBe(0);
        });
    });

    describe('summarize', () => {
        it('should report every thread when none was skipped', () => {
            expect(ToastBatchHelper.summarize({ threadIds: ['t1', 't2'], messageIds: [], skippedCount: 0 })).toEqual({
                isThreadScope: true,
                submittedCount: 2,
                displayCount: 2,
                isPartial: false,
                isNone: false,
            });
        });

        it('should report the updated threads only when some were skipped', () => {
            expect(ToastBatchHelper.summarize({ threadIds: ['t1', 't2', 't3'], messageIds: [], skippedCount: 1 })).toMatchObject({
                displayCount: 2,
                isPartial: true,
                isNone: false,
            });
        });

        it('should report no thread when all were skipped', () => {
            expect(ToastBatchHelper.summarize({ threadIds: ['t1', 't2'], messageIds: [], skippedCount: 2 })).toMatchObject({
                displayCount: 0,
                isPartial: false,
                isNone: true,
            });
        });

        it('should report no message when all were skipped', () => {
            expect(ToastBatchHelper.summarize({ threadIds: [], messageIds: ['m1', 'm2'], skippedCount: 2 })).toMatchObject({
                isThreadScope: false,
                submittedCount: 2,
                displayCount: 0,
                isNone: true,
            });
        });

        it('should report every item as updated when the skipped count is unknown', () => {
            expect(ToastBatchHelper.summarize({ threadIds: ['t1', 't2'], messageIds: [] })).toMatchObject({
                displayCount: 2,
                isPartial: false,
                isNone: false,
            });
        });
    });
});
