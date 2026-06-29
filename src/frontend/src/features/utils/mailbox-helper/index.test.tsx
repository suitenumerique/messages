import MailboxHelper from './index';
import type { MailboxAdmin } from '@/features/api/gen';

describe('MailboxHelper', () => {
  describe('toString', () => {
    it('should format email from MailboxAdmin shape', () => {
      const mailbox = { local_part: 'john.doe', domain_name: 'example.com' } as unknown as MailboxAdmin;
      const result = MailboxHelper.toString(mailbox);
      expect(result).toBe('john.doe@example.com');
    });
  });

  describe('sortByKind', () => {
    it('should put personal mailboxes first, then sort each group by email', () => {
      const mailboxes = [
        { is_identity: false, email: 'support@example.com' },
        { is_identity: true, email: 'zoe@example.com' },
        { is_identity: false, email: 'contact@example.com' },
        { is_identity: true, email: 'alice@example.com' },
      ];

      const result = MailboxHelper.sortByKind(mailboxes);

      expect(result.map((m) => m.email)).toEqual([
        'alice@example.com',
        'zoe@example.com',
        'contact@example.com',
        'support@example.com',
      ]);
    });

    it('should not mutate the input array', () => {
      const mailboxes = [
        { is_identity: false, email: 'b@example.com' },
        { is_identity: true, email: 'a@example.com' },
      ];

      MailboxHelper.sortByKind(mailboxes);

      expect(mailboxes[0].email).toBe('b@example.com');
    });
  });

  describe('showSeparatorAfter', () => {
    const sorted = [
      { is_identity: true },
      { is_identity: false },
      { is_identity: false },
    ];

    it('should return true on the last personal mailbox before a shared one', () => {
      expect(MailboxHelper.showSeparatorAfter(sorted, 0)).toBe(true);
    });

    it('should return false between two shared mailboxes', () => {
      expect(MailboxHelper.showSeparatorAfter(sorted, 1)).toBe(false);
    });

    it('should return false on the last mailbox', () => {
      expect(MailboxHelper.showSeparatorAfter(sorted, 2)).toBe(false);
    });

    it('should return false when every mailbox is personal', () => {
      const onlyPersonal = [{ is_identity: true }, { is_identity: true }];
      expect(MailboxHelper.showSeparatorAfter(onlyPersonal, 0)).toBe(false);
    });
  });
});

