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
    """A factory to random mail domains for testing purposes."""

    class Meta:
        model = models.MailDomain

    name = factory.Faker("domain_name")


class MailboxFactory(factory.django.DjangoModelFactory):
    """A factory to random mailboxes for testing purposes."""

    class Meta:
        model = models.Mailbox

    domain = factory.SubFactory(MailDomainFactory)
    local_part = factory.Sequence(lambda n: f"john.doe{n!s}")


class MailboxAccessFactory(factory.django.DjangoModelFactory):
    """A factory to random mailbox accesses for testing purposes."""

    class Meta:
        model = models.MailboxAccess

    mailbox = factory.SubFactory(MailboxFactory)
    user = factory.SubFactory(UserFactory)
    permission = factory.fuzzy.FuzzyChoice(
        [permission[0] for permission in models.MailboxPermissionChoices.choices]
    )


class ThreadFactory(factory.django.DjangoModelFactory):
    """A factory to random threads for testing purposes."""

    class Meta:
        model = models.Thread

    subject = factory.Faker("sentence")
    snippet = factory.Faker("text")
    mailbox = factory.SubFactory(MailboxFactory)


class ContactFactory(factory.django.DjangoModelFactory):
    """A factory to random contacts for testing purposes."""

    class Meta:
        model = models.Contact

    name = factory.Faker("name")
    email = factory.Faker("email")
    owner = factory.SubFactory(MailboxFactory)


class MessageFactory(factory.django.DjangoModelFactory):
    """A factory to random messages for testing purposes."""

    class Meta:
        model = models.Message

    thread = factory.SubFactory(ThreadFactory)
    subject = factory.Faker("sentence")
    sender = factory.SubFactory(ContactFactory)
    received_at = factory.LazyAttribute(lambda o: timezone.now())
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
