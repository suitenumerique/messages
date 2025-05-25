"""Forms for the core app."""

from django import forms

from core.models import Mailbox


class MessageImportForm(forms.Form):
    """Form for importing EML or MBOX files in the admin interface."""

    import_file = forms.FileField(
        label="Import File",
        help_text="Select an EML or MBOX file to import",
        widget=forms.FileInput(attrs={"accept": ".eml,.mbox"}),
    )
    recipient = forms.ModelChoiceField(
        queryset=Mailbox.objects.all(),
        label="Mailbox Recipient",
        help_text="Select the recipient for this message",
        required=True,
        empty_label=None,
    )

    def clean_import_file(self):
        """Validate the uploaded file."""
        file = self.cleaned_data.get("import_file")
        if not file:
            return None

        if not file.name.endswith((".eml", ".mbox")):
            raise forms.ValidationError(
                "File must be either an EML (.eml) or MBOX (.mbox) file"
            )
        return file


class IMAPImportForm(forms.Form):
    """Form for importing messages from IMAP server."""

    recipient = forms.ModelChoiceField(
        queryset=Mailbox.objects.all(),
        label="Mailbox Recipient",
        help_text="Select the recipient for this message",
        required=True,
        empty_label=None,
    )

    imap_server = forms.CharField(
        label="IMAP Server",
        help_text="IMAP server hostname (e.g. imap.gmail.com)",
        required=True,
    )
    imap_port = forms.IntegerField(
        label="IMAP Port",
        help_text="IMAP server port (e.g. 993 for SSL)",
        required=True,
        initial=993,
        min_value=0,
    )
    username = forms.EmailField(
        label="Email Address",
        help_text="Your email address for IMAP login",
        required=True,
    )
    password = forms.CharField(
        label="Password",
        help_text="Your IMAP password or app password",
        required=True,
        widget=forms.PasswordInput(),
    )
    use_ssl = forms.BooleanField(
        label="Use SSL",
        help_text="Whether to use SSL for the connection",
        required=False,
        initial=True,
    )
    folder = forms.CharField(
        label="Folder",
        help_text="IMAP folder to import from (e.g. INBOX)",
        required=True,
        initial="INBOX",
    )
    max_messages = forms.IntegerField(
        label="Maximum Messages",
        help_text="Maximum number of messages to import (0 for all)",
        required=False,
        initial=0,
        min_value=0,
    )
