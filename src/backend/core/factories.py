# ruff: noqa: S311
"""
Core application factories
"""

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone

import factory.fuzzy
from faker import Faker

from core import models

fake = Faker()


class UserFactory(factory.django.DjangoModelFactory):
    """A factory to random users for testing purposes."""

    class Meta:
        model = models.User
        skip_postgeneration_save = True

    sub = factory.Sequence(lambda n: f"user{n!s}")
    email = factory.Faker("email")
    full_name = factory.Faker("name")
    short_name = factory.Faker("first_name")
    language = factory.fuzzy.FuzzyChoice([lang[0] for lang in settings.LANGUAGES])
    password = make_password("password")


class ParentNodeFactory(factory.declarations.ParameteredAttribute):
    """Custom factory attribute for setting the parent node."""

    def generate(self, step, params):
        """
        Generate a parent node for the factory.

        This method is invoked during the factory's build process to determine the parent
        node of the current object being created. If `params` is provided, it uses the factory's
        metadata to recursively create or fetch the parent node. Otherwise, it returns `None`.
        """
        if not params:
            return None
        subfactory = step.builder.factory_meta.factory
        return step.recurse(subfactory, params)


class MailDomainFactory(factory.django.DjangoModelFactory):
    """A factory to random mail domains for testing purposes, ensuring uniqueness."""

    class Meta:
        model = models.MailDomain

    name = factory.Sequence(lambda n: f"example{n}.com")


class MailboxFactory(factory.django.DjangoModelFactory):
    """A factory to random mailboxes for testing purposes."""

    class Meta:
        model = models.Mailbox
        skip_postgeneration_save = True

    name = factory.Faker("name")
    domain = factory.SubFactory(MailDomainFactory)
    local_part = factory.Sequence(lambda n: f"john.doe{n!s}")

    @factory.post_generation
    def users_read(self, create, users, **kwargs):
        """
        Optionally assign users with read access to this mailbox.
        Usage: MailboxFactory(users_read=[user1, user2])
        """
        if not create or not users:
            return
        for user in users:
            models.MailboxAccess.objects.create(
                mailbox=self, user=user, role=models.MailboxRoleChoices.VIEWER
            )

    @factory.post_generation
    def users_admin(self, create, users, **kwargs):
        """
        Optionally assign users with admin access to this mailbox.
        Usage: MailboxFactory(users_admin=[user1, user2])
        """
        if not create or not users:
            return
        for user in users:
            models.MailboxAccess.objects.create(
                mailbox=self,
                user=user,
                role=models.MailboxRoleChoices.ADMIN,
            )


class MailboxAccessFactory(factory.django.DjangoModelFactory):
    """A factory to random mailbox accesses for testing purposes."""

    class Meta:
        model = models.MailboxAccess

    mailbox = factory.SubFactory(MailboxFactory)
    user = factory.SubFactory(UserFactory)
    role = factory.fuzzy.FuzzyChoice(
        [role[0] for role in models.MailboxRoleChoices.choices]
    )


class ThreadFactory(factory.django.DjangoModelFactory):
    """A factory to random threads for testing purposes."""

    class Meta:
        model = models.Thread

    subject = factory.Faker("sentence")
    snippet = factory.Faker("text")


class ThreadAccessFactory(factory.django.DjangoModelFactory):
    """A factory to random thread accesses for testing purposes."""

    class Meta:
        model = models.ThreadAccess

    thread = factory.SubFactory(ThreadFactory)
    mailbox = factory.SubFactory(MailboxFactory)
    role = factory.fuzzy.FuzzyChoice(
        [role[0] for role in models.ThreadAccessRoleChoices.choices]
    )


class ContactFactory(factory.django.DjangoModelFactory):
    """A factory to random contacts for testing purposes."""

    class Meta:
        model = models.Contact
        django_get_or_create = ("email", "mailbox")

    name = factory.Faker("name")
    email = factory.Faker("email")
    mailbox = factory.SubFactory(MailboxFactory)


class MessageFactory(factory.django.DjangoModelFactory):
    """A factory to random messages for testing purposes."""

    class Meta:
        model = models.Message

    thread = factory.SubFactory(ThreadFactory)
    subject = factory.Faker("sentence")
    sender = factory.SubFactory(ContactFactory)
    created_at = factory.LazyAttribute(lambda o: timezone.now())
    mime_id = factory.Sequence(lambda n: f"message{n!s}")


class MessageRecipientFactory(factory.django.DjangoModelFactory):
    """A factory to random message recipients for testing purposes."""

    class Meta:
        model = models.MessageRecipient

    message = factory.SubFactory(MessageFactory)
    contact = factory.SubFactory(ContactFactory)
    type = factory.fuzzy.FuzzyChoice(
        [type[0] for type in models.MessageRecipientTypeChoices.choices]
    )
