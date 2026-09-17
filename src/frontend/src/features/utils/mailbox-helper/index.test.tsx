import MailboxHelper from './index';
import type { Mailbox, MailboxAdmin } from '@/features/api/gen';
import { MailboxRoleChoices } from '@/features/api/gen';
import { LAST_ACTIVE_MAILBOX_KEY } from '@/features/config/constants';

const makeMailbox = (overrides: Partial<Mailbox> & Pick<Mailbox, 'id' | 'email'>): Mailbox => ({
  name: null,
  domain_id: 'domain',
  is_identity: false,
  is_shared: true,
  role: MailboxRoleChoices.viewer,
  count_unread_threads: 0,
  count_threads: 0,
  count_delivering: 0,
  count_unread_mentions: 0,
  count_assigned: 0,
  abilities: {} as Mailbox['abilities'],
  ...overrides,
});

const support = makeMailbox({ id: 'support', email: 'support@example.local', role: MailboxRoleChoices.admin });
const sales = makeMailbox({ id: 'sales', email: 'sales@example.local', role: MailboxRoleChoices.admin });
const personal = makeMailbox({ id: 'personal', email: 'user1@example.local', is_identity: true, is_shared: false, role: MailboxRoleChoices.editor });
const readOnly = makeMailbox({ id: 'readonly', email: 'archive@example.local', role: MailboxRoleChoices.viewer });

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

  describe('last active mailbox storage', () => {
    beforeEach(() => {
      localStorage.clear();
    });

    it('round-trips the mailbox for the same user', () => {
      MailboxHelper.persistLastActiveMailbox('user-a', 'mailbox-1');
      expect(MailboxHelper.readLastActiveMailboxId('user-a')).toBe('mailbox-1');
    });

    it('ignores a record persisted by another user', () => {
      MailboxHelper.persistLastActiveMailbox('user-a', 'mailbox-1');
      expect(MailboxHelper.readLastActiveMailboxId('user-b')).toBeUndefined();
    });

    it('returns nothing without a user or a record', () => {
      expect(MailboxHelper.readLastActiveMailboxId(undefined)).toBeUndefined();
      expect(MailboxHelper.readLastActiveMailboxId('user-a')).toBeUndefined();
    });

    it('survives a corrupted record', () => {
      localStorage.setItem(LAST_ACTIVE_MAILBOX_KEY, '{not json');
      expect(MailboxHelper.readLastActiveMailboxId('user-a')).toBeUndefined();
      localStorage.setItem(LAST_ACTIVE_MAILBOX_KEY, JSON.stringify({ userId: 'user-a' }));
      expect(MailboxHelper.readLastActiveMailboxId('user-a')).toBeUndefined();
    });

    it('clears the record', () => {
      MailboxHelper.persistLastActiveMailbox('user-a', 'mailbox-1');
      MailboxHelper.clearLastActiveMailbox();
      expect(localStorage.getItem(LAST_ACTIVE_MAILBOX_KEY)).toBeNull();
    });
  });

  describe('findPersonalMailbox', () => {
    it('matches an identity mailbox on the local part, case-insensitively', () => {
      expect(MailboxHelper.findPersonalMailbox([support, personal], 'User1@other.tld')).toBe(personal);
    });

    it('ignores non-identity mailboxes sharing the local part', () => {
      const alias = makeMailbox({ id: 'alias', email: 'user1@alias.local', is_identity: false });
      expect(MailboxHelper.findPersonalMailbox([alias], 'user1@example.local')).toBeUndefined();
    });

    it('prefers the exact address when several domains share the local part', () => {
      const other = makeMailbox({ id: 'other', email: 'user1@other.local', is_identity: true });
      expect(MailboxHelper.findPersonalMailbox([other, personal], 'user1@example.local')).toBe(personal);
      expect(MailboxHelper.findPersonalMailbox([other, personal], 'user1@unknown.local')).toBe(other);
    });

    it('returns nothing without a user email', () => {
      expect(MailboxHelper.findPersonalMailbox([personal], null)).toBeUndefined();
      expect(MailboxHelper.findPersonalMailbox([personal], undefined)).toBeUndefined();
    });
  });

  describe('findMostPrivilegedMailbox', () => {
    it('picks the last listed mailbox of the highest role', () => {
      expect(MailboxHelper.findMostPrivilegedMailbox([support, sales, personal, readOnly])).toBe(sales);
      expect(MailboxHelper.findMostPrivilegedMailbox([readOnly, personal])).toBe(personal);
    });

    it('falls back to the last mailbox when no role is set', () => {
      const noRole = makeMailbox({ id: 'norole', email: 'x@example.local', role: null });
      expect(MailboxHelper.findMostPrivilegedMailbox([noRole])).toBe(noRole);
      expect(MailboxHelper.findMostPrivilegedMailbox([])).toBeUndefined();
    });
  });

  describe('resolveSelectedMailbox', () => {
    const mailboxes = [support, sales, personal, readOnly];

    it('returns null without mailboxes', () => {
      expect(MailboxHelper.resolveSelectedMailbox([], { routeMailboxId: 'support' })).toBeNull();
    });

    it('prefers the mailbox addressed by the route', () => {
      expect(MailboxHelper.resolveSelectedMailbox(mailboxes, {
        routeMailboxId: 'readonly',
        lastActiveMailboxId: 'support',
        userEmail: 'user1@example.local',
      })).toBe(readOnly);
    });

    it('falls back to the last active mailbox when the route matches nothing', () => {
      expect(MailboxHelper.resolveSelectedMailbox(mailboxes, {
        routeMailboxId: 'revoked',
        lastActiveMailboxId: 'support',
        userEmail: 'user1@example.local',
      })).toBe(support);
    });

    it('falls back to the personal mailbox when the last active one is gone', () => {
      expect(MailboxHelper.resolveSelectedMailbox(mailboxes, {
        lastActiveMailboxId: 'revoked',
        userEmail: 'user1@example.local',
      })).toBe(personal);
    });

    it('falls back to the most privileged mailbox as a last resort', () => {
      expect(MailboxHelper.resolveSelectedMailbox(mailboxes, { userEmail: 'nobody@example.local' })).toBe(sales);
      expect(MailboxHelper.resolveSelectedMailbox(mailboxes, {})).toBe(sales);
    });
  });
});
