import { Thread } from "../api/gen/models";
import ThreadHelper from "./thread-helper";

describe('ThreadHelper', () => {
  describe('isUnread', () => {
    it('should return true when all messages are unread', () => {
    const thread = {
        count_messages: 5,
        count_unread: 5
    } as Thread;
    expect(ThreadHelper.isUnread(thread, true)).toBe(true);
    expect(ThreadHelper.isUnread(thread, false)).toBe('full');
    expect(ThreadHelper.isUnread(thread)).toBe('full');
    });

    it('should return true when some messages are unread', () => {
    const thread = {
        count_messages: 5,
        count_unread: 3
    } as Thread;
    expect(ThreadHelper.isUnread(thread, true)).toBe(true);
    expect(ThreadHelper.isUnread(thread, false)).toBe('partial');
    expect(ThreadHelper.isUnread(thread)).toBe('partial');
    });

    it('should return false when no messages are unread', () => {
    const thread = {
        count_messages: 5,
        count_unread: 0
    } as Thread;
    expect(ThreadHelper.isUnread(thread, true)).toBe(false);
    expect(ThreadHelper.isUnread(thread, false)).toBe(null);
    expect(ThreadHelper.isUnread(thread)).toBe(null);
    });

    describe('edge cases', () => {
      it('should handle undefined count values', () => {
        const thread = {
          count_messages: undefined,
          count_unread: undefined
        } as Thread;
        expect(ThreadHelper.isUnread(thread, false)).toBe(null);
        expect(ThreadHelper.isUnread(thread, true)).toBe(false);
      });

      it('should handle zero messages', () => {
        const thread = {
          count_messages: 0,
          count_unread: 0
        } as Thread;
        expect(ThreadHelper.isUnread(thread, false)).toBe(null);
        expect(ThreadHelper.isUnread(thread, true)).toBe(false);
      });
    });
  });
}); 