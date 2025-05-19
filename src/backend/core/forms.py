"""Forms for the core app."""

from django import forms

from core.models import Mailbox


class EmlImportForm(forms.Form):
    """Form for importing EML files in the admin interface."""

    eml_file = forms.FileField(
        label="EML File",
        help_text="Select an EML file to import",
        widget=forms.FileInput(attrs={"accept": ".eml"}),
    )
    recipient = forms.ModelChoiceField(
        queryset=Mailbox.objects.all(),
        label="Mailbox Recipient",
        help_text="Select the recipient for this message",
        required=True,
        empty_label=None,
    )
