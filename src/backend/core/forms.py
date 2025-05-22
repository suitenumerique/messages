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
