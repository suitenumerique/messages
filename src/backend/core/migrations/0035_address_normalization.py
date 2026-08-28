"""Address normalization: field validators, then a fold of existing rows.

Two validator changes, neither of which touches a column's type or length:

- ``Contact.email`` was an ``EmailField``, whose validator rejects an RFC
  6531 local part. We accept SMTPUTF8 sessions inbound, so that validator
  was discarding the real sender of mail we had already taken delivery of.
- ``MailDomain.name`` now validates per label rather than per string.

Then the data fold. Mailbox local parts, mail domain names and user identity
emails are now stored ASCII-lowercased (see ``core.mda.addresses``) and
looked up by exact match. Rows written before that would stop resolving.

Folding can collide with a row that already holds the lowercase form. Those
are left untouched and reported: merging two mailboxes (or two users) means
moving threads, accesses and contacts between them, which is an operator
decision, not something a migration should guess at.

The translate table is inlined rather than imported from
``core.mda.addresses`` so this migration keeps replaying identically if that
module changes.
"""

import string

from django.db import migrations, models
import django.core.validators

import idna

import core.mda.addresses

ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def fold_address(value: str) -> str:
    """Mirror of ``normalize_address`` as it stood when this ran.

    Inlined for the same reason as the translate table: the fold has to keep
    replaying identically if ``core.mda.addresses`` changes.

    The domain half matters here. ``EmailField``, which guarded these columns
    before this migration, accepts a U-label (``john@münchen.de``), while every
    lookup now canonicalizes to the A-label. ASCII-folding alone would leave
    such a row unreachable by its own owner.
    """
    value = value.strip()
    local_part, separator, domain = value.rpartition("@")
    if not separator:
        return value.translate(ASCII_LOWER)
    domain = domain.translate(ASCII_LOWER)
    if not domain.isascii():
        try:
            domain = idna.encode(domain, uts46=True).decode("ascii")
        except idna.IDNAError:
            # No A-label exists, so nothing would match it either way. Left as
            # written rather than mangled.
            pass
    return f"{local_part.translate(ASCII_LOWER)}@{domain}"


def _report(collision_ids, label):
    """Report collisions by primary key only.

    Migration output lands in deploy logs, so it names rows rather than
    addresses: a local part is PII, and the operator needs the id to look
    the row up and merge it by hand anyway.
    """
    if collision_ids:
        listed = ", ".join(str(pk) for pk in sorted(collision_ids)[:20])
        more = "" if len(collision_ids) <= 20 else ", ..."
        print(
            f"\n  WARNING: {len(collision_ids)} {label} left unfolded, a "
            f"lowercase twin already exists. Affected ids: {listed}{more}"
        )


def fold_maildomains(apps, schema_editor):
    """Lowercase mail domain names (DNS is case-insensitive)."""
    MailDomain = apps.get_model("core", "MailDomain")
    taken = set(MailDomain.objects.values_list("name", flat=True))
    collisions = []
    for domain in MailDomain.objects.all().iterator():
        folded = domain.name.translate(ASCII_LOWER)
        if folded == domain.name:
            continue
        if folded in taken:
            collisions.append(domain.pk)
            continue
        taken.discard(domain.name)
        taken.add(folded)
        domain.name = folded
        domain.save(update_fields=["name"])
    _report(collisions, "mail domains")


def fold_mailboxes(apps, schema_editor):
    """Lowercase mailbox local parts, and their own identity contact with them."""
    Mailbox = apps.get_model("core", "Mailbox")
    Contact = apps.get_model("core", "Contact")

    taken = set(Mailbox.objects.values_list("domain_id", "local_part"))
    collisions = []
    for mailbox in (
        Mailbox.objects.select_related("domain", "contact").all().iterator()
    ):
        folded = mailbox.local_part.translate(ASCII_LOWER)
        if folded == mailbox.local_part:
            continue
        if (mailbox.domain_id, folded) in taken:
            collisions.append(mailbox.pk)
            continue
        taken.discard((mailbox.domain_id, mailbox.local_part))
        taken.add((mailbox.domain_id, folded))
        mailbox.local_part = folded
        mailbox.save(update_fields=["local_part"])

        # Keep the mailbox's own contact — the one that becomes the From
        # header of everything it sends — on the same address as the mailbox.
        contact = mailbox.contact
        if contact is None:
            continue
        new_email = f"{folded}@{mailbox.domain.name}"
        if contact.email == new_email:
            continue
        twin = Contact.objects.filter(mailbox=mailbox, email=new_email).first()
        if twin is not None:
            mailbox.contact = twin
            mailbox.save(update_fields=["contact"])
        else:
            contact.email = new_email
            contact.save(update_fields=["email"])
    _report(collisions, "mailboxes")


def fold_users(apps, schema_editor):
    """Lowercase the two identity addresses users are matched on."""
    User = apps.get_model("core", "User")

    # ``email`` carries no unique constraint, so folding cannot fail — it can
    # merge. Two rows differing only in case become the same string, and
    # ``get_user_by_sub_or_email`` resolves stubs with ``.get(email=...)``,
    # which then raises MultipleObjectsReturned and 500s that user's login.
    # Folded anyway (leaving one unfolded would strand it instead), but
    # reported, because only an operator can decide which row survives.
    # Which canonical forms more than one spelling maps onto, computed before
    # anything moves. Accumulating this during the fold would only catch a
    # collision when the row that stays put happens to be scanned first, and
    # ``.iterator()`` promises no order — so half of them would go unreported,
    # which is the half an operator finds out about from a locked-out user.
    first_spelling = {}
    ambiguous = set()
    for email in (
        User.objects.exclude(email=None)
        .exclude(email="")
        .values_list("email", flat=True)
        .iterator()
    ):
        canonical = fold_address(email)
        if first_spelling.setdefault(canonical, email) != email:
            ambiguous.add(canonical)

    merged = []
    for user in User.objects.exclude(email=None).exclude(email="").iterator():
        folded = fold_address(user.email)
        if folded == user.email:
            # Already canonical: it is the row others land on, not one that
            # moves. Rows that already shared an address did so before this
            # ran and are legal under OIDC_ALLOW_DUPLICATE_EMAILS.
            continue
        if folded in ambiguous:
            merged.append(user.pk)
        user.email = folded
        user.save(update_fields=["email"])
    if merged:
        listed = ", ".join(str(pk) for pk in sorted(merged)[:20])
        more = "" if len(merged) <= 20 else ", ..."
        print(
            f"\n  WARNING: folding moved {len(merged)} user(s) onto an identity "
            f"email another row already held. A login that has to resolve by "
            f"email — claiming a sub-less stub, or the OIDC email fallback — "
            f"now refuses rather than guess between them. Affected ids: "
            f"{listed}{more}"
        )

    # admin_email is unique, so a folded value can collide.
    taken = set(
        User.objects.exclude(admin_email=None).values_list("admin_email", flat=True)
    )
    collisions = []
    for user in (
        User.objects.exclude(admin_email=None).exclude(admin_email="").iterator()
    ):
        folded = fold_address(user.admin_email)
        if folded == user.admin_email:
            continue
        if folded in taken:
            collisions.append(user.pk)
            continue
        taken.discard(user.admin_email)
        taken.add(folded)
        user.admin_email = folded
        user.save(update_fields=["admin_email"])
    _report(collisions, "admin emails")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0034_channel_lookup_hash"),
    ]

    operations = [
        # Validators first, data second: the fold below writes through
        # historical models, which do not run these anyway, but keeping
        # schema before data is the order the rest of the project uses.
        migrations.AlterField(
            model_name="contact",
            name="email",
            field=models.CharField(
                max_length=254,
                validators=[core.mda.addresses.AddrSpecValidator()],
                verbose_name="email",
            ),
        ),
        migrations.AlterField(
            model_name="maildomain",
            name="name",
            field=models.CharField(
                max_length=253,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message=(
                            "Enter a valid domain name. This value may contain "
                            "only lowercase letters, numbers, dots and - "
                            "characters; each label must start and end with a "
                            "letter or a number and be at most 63 characters."
                        ),
                        regex=(
                            r"^(?=.{2,253}$)"
                            r"(?=[a-z0-9-]{1,63}(?:\.|$))[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
                            r"(?:\.(?=[a-z0-9-]{1,63}(?:\.|$))"
                            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$"
                        ),
                    )
                ],
                verbose_name="name",
            ),
        ),
        migrations.RunPython(fold_maildomains, migrations.RunPython.noop),
        migrations.RunPython(fold_mailboxes, migrations.RunPython.noop),
        migrations.RunPython(fold_users, migrations.RunPython.noop),
    ]
