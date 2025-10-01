# Internationalization (i18n)

Messages supports localization and internationalization on both
**backend** and **frontend**:

-   **Backend**: powered by [Django
    i18n](https://docs.djangoproject.com/en/5.2/topics/i18n/translation/)
-   **Frontend**: powered by [i18next](https://www.i18next.com/),
    [react-i18next](https://react.i18next.com/), and
    [i18next-cli](https://github.com/i18next/i18next-cli)

------------------------------------------------------------------------

## Development Workflow

### Best practices during development

-   **Backend**: always write strings in **English** using Django's
    translation utilities.
-   **Frontend**: always write strings in **English** using `i18next`.

👉 Translations are updated **before each release**.

-   Backend strings are stored in:
    `src/backend/locale/{locale}/LC_MESSAGES/django.po`
-   Frontend strings are stored in:
    `src/frontend/public/locales/{ns}/{locale}.json`

------------------------------------------------------------------------

### Extraction and compilation of translations

The extraction and compilation process is **automated by the CI
pipeline**:

-   Whenever the `main` branch is updated, the CI
    will:
    -   extract translations
    -   upload them to **Crowdin**

-   Whenever a branch with the prefix `release/` is created, the CI
    will:
    -   download and compile the updated translations
    -   create a pull request with the changes

Those processes can also be triggered manually.

#### Running the process locally

You can perform these steps locally using the **Makefile**.
⚠️ Make sure you have Crowdin environment variables configured in:
`.env/development/crowdin`
and that you have **sufficient permissions** on the Crowdin project.

-   **Extract and upload translations to Crowdin:**

``` sh
make i18n-generate-and-upload
```

-   **Download and compile translations:**

``` sh
make i18n-download-and-compile
```

#### Updating translations locally (not recommended)

It is possible (but discouraged) to manually edit translations locally:

1.  Generate translation files:

    ``` sh
    make i18n-generate
    ```

2.  Edit missing translations directly in the generated files.

3.  Generate translation files:

    ``` sh
    make i18n-compile
    ```

4.  Commit your changes.

⚠️ **Warning: these local changes are likely to be overwritten**
**by the next Crowdin update.**

------------------------------------------------------------------------

## Contributing as a translator or proofreader

We use [Crowdin](https://crowdin.com) to manage translations.
It allows translators and proofreaders to contribute in the languages
they know best.

👉 For more information, see the [Crowdin
documentation](https://support.crowdin.com).

------------------------------------------------------------------------

### Adding a new language

If the language you need is not yet available:

-   Click **Request New Language** on the [project
    page](https://crowdin.com/project/lasuite-messages).
-   We will review and may add it.

⚠️ If you request a new language, you are expected to help keep it **up
to date** whenever strings are added or modified --- especially before
each release.

If your language already exists in a different variant (e.g. Brazilian
Portuguese vs. European Portuguese), consider contributing to the
existing one unless you have enough resources to maintain a separate
variant.
