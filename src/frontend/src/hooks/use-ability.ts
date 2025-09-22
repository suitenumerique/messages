import {
  Mailbox,
  MailDomainAdmin,
  Thread,
  ThreadAccessRoleChoices,
} from "@/features/api/gen";
import { useAuth } from "@/features/auth";

enum MailboxAbilities {
  CAN_SEND_MESSAGES = "send_messages",
  CAN_WRITE_MESSAGES = "patch",
  CAN_MANAGE_MAILBOX_LABELS = "manage_labels",
}

enum UserAbilities {
  CAN_VIEW_DOMAIN_ADMIN = "view_maildomains",
  CAN_CREATE_MAILDOMAINS = "create_maildomains",
  CAN_MANAGE_SOME_MAILDOMAIN_ACCESSES = "manage_maildomain_accesses",
}

enum MaildomainAbilities {
  CAN_MANAGE_MAILDOMAIN_MAILBOXES = "manage_mailboxes",
  CAN_MANAGE_MAILDOMAIN_ACCESSES = "manage_accesses",
}

enum ThreadAccessAbilities {
  CAN_MANAGE_THREAD_ACCESS = "manage_thread_access",
}

export const Abilities = {
  ...UserAbilities,
  ...MailboxAbilities,
  ...MaildomainAbilities,
  ...ThreadAccessAbilities,
};

type AbilityKey = (typeof Abilities)[keyof typeof Abilities];

type ResourceWithAbilities = {
  abilities: Record<string, boolean>;
};

function useAbility(ability: UserAbilities): boolean;
function useAbility(
  ability: MailboxAbilities,
  resource: Mailbox | null
): boolean;
function useAbility(
  ability: MaildomainAbilities,
  resource: MailDomainAdmin | null
): boolean;
function useAbility(
  ability: ThreadAccessAbilities,
  resource: [Mailbox, Thread]
): boolean;
function useAbility(
  ability: AbilityKey,
  resource?: ResourceWithAbilities | [Mailbox, Thread] | null
) {
  const { user } = useAuth();
  if (
    resource === undefined &&
    Object.values(UserAbilities).includes(ability as UserAbilities)
  )
    resource = user;
  const isResourceInvalid =
    !resource || (Array.isArray(resource) && resource.some((r) => r === null));
  if (isResourceInvalid) return false;

  switch (ability) {
    case Abilities.CAN_SEND_MESSAGES:
    case Abilities.CAN_WRITE_MESSAGES:
    case Abilities.CAN_VIEW_DOMAIN_ADMIN:
    case Abilities.CAN_CREATE_MAILDOMAINS:
    case Abilities.CAN_MANAGE_MAILBOX_LABELS:
    case Abilities.CAN_MANAGE_MAILDOMAIN_MAILBOXES:
    case Abilities.CAN_MANAGE_MAILDOMAIN_ACCESSES:
    case Abilities.CAN_MANAGE_SOME_MAILDOMAIN_ACCESSES:
      return (resource as ResourceWithAbilities).abilities[ability] === true;
    case Abilities.CAN_MANAGE_THREAD_ACCESS:
      const [mailbox, thread] = resource as [Mailbox, Thread];
      return (
        mailbox.abilities[Abilities.CAN_SEND_MESSAGES] === true &&
        thread.user_role === ThreadAccessRoleChoices.editor
      );
    default:
      throw new Error(`Ability ${ability} does not exist in Abilities enum.`);
  }
}

export default useAbility;
