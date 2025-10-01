# Internationalization
---

Messages has built-in localization and internationalization:

- On the backend, i18n is built [on the shoulders of Django](https://docs.djangoproject.com/en/5.2/topics/i18n/translation/),
- On the frontend, we use [i18next](https://www.i18next.com/), [react-i18next](https://react.i18next.com/) and [i18next-cli](https://github.com/i18next/i18next-cli).

## Development Workflow

### Develop

During development, you should not care about translations. From backend use translation utils
to write our translated strings in English. On frontend, use i18next to write our translated
strings in English.

Translations will be updated before each release.

Backend strings are stored in the `src/backend/locale/django.pot` file.

Frontend strings are stored in the `src/frontend/public/locales/{ns}/en-US.json` file.

### Extraction / Compilation of translations

The process to extract translations then upload them to Crowdin is automated by a CI pipeline.
Download and compile process can be run manually through the CI.

But you can also run those processes locally through the Makefile.
**You will need to have setup Crowdin envs in the `.env/development/crowdin` file and has sufficient**
**permissions to upload and download translations from the Crowdin project.**

To extract translations and upload them to Crowdin, run:
```shellscript
make i18n-generate-and-upload
```
To download translations and compile them, run:
```shellscript
make i18n-download-and-compile
```


## Contributing as a translator or proof-reader

We use the [Crowdin](https://crowdin.com) web platform to translate Messages to different languages.
It allows translators and proof-readers to contribute on translations in the languages they master.

### Sign-up on Crowdin

If you don't have an account on Crowdin already, go to https://accounts.crowdin.com/register and
fill out the form to create a free account.

### Join the "La Suite Messages" project

Now that you have an account on Crowdin,
[look for the project called "La Suite Messages"](https://crowdin.com/project/lasuite-messages),
select the language on which you wish to contribute and click the "Join" button as demonstrated below.

We will then review you application and you should soon start translating strings!

For more information on how Crowdin works, you can refer to
[their documentation](https://support.crowdin.com).

### Add a new language

If the language you want is not yet translated, you can request a new one by clicking the
"Request New Language" button on [Message's Crowdin profile page](https://crowdin.com/project/lasuite-messages) and we will consider adding it.

If you request a new language, the community will expect you to keep this language
up-to-date each time strings are modified or new strings are added, and this before each
release.

Before asking for a new language, make sure it does not already exist. If your language already
exists in another variant (e.g. Brazilian portuguese vs Portugal portuguese), you may consider
contributing on the existing language if your resources to contribute are limited.
