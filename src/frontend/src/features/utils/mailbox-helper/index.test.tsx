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
});

