# Internationalization (i18n)

Only the **frontend** is translated today. It is powered by
[i18next](https://www.i18next.com/),
[react-i18next](https://react.i18next.com/), and
[i18next-cli](https://github.com/i18next/i18next-cli).

The **backend has no translation catalog**: `USE_I18N` is `False`
(`src/backend/messages/settings.py`), there are no `locale/` directories, and
no `gettext` call anywhere in `core/`. Every string the backend emits is
English. See [Backend strings](#backend-strings-not-translated-yet) below.

------------------------------------------------------------------------

## Development Workflow

### Best practices during development

-   Always write strings in **English** using `i18next`.

👉 Translations are updated **before each release**.

-   Frontend strings are stored in:
    `src/frontend/public/locales/{ns}/{locale}.json`

------------------------------------------------------------------------

### Extraction and download of translations

The process is **automated by the CI pipeline**:

-   Whenever the `main` branch is updated, the CI
    will:
    -   extract translations
    -   upload them to **Crowdin**

-   Whenever a branch with the prefix `release/` is created, the CI
    will:
    -   download the updated translations
    -   create a pull request with the changes

Those processes can also be triggered manually.

#### Running the process locally

You can perform these steps locally using the **Makefile**.
⚠️ Make sure you have Crowdin environment variables configured in:
`deploy/env/crowdin.local`
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

3.  Commit your changes.

The JSON catalogs are read as-is at runtime, so there is no compilation
step.

⚠️ **Warning: these local changes are likely to be overwritten**
**by the next Crowdin update.**

------------------------------------------------------------------------

## Backend strings (not translated yet)

Some user-facing text is produced by the backend rather than the frontend, and
is therefore **English only**. The main case today is the mailbox export
notification email (`core/services/exporter/tasks.py`,
`_create_notification_message`), which carries the download link to the
mailbox the requester picked as recipient when queueing the export.

### Why not Django i18n

Reintroducing `gettext` is not the plan: it means a second translation format
(`.po`), a compile step, a second Crowdin file type, and `USE_I18N = True`
across the whole app, for a handful of strings.

### Why the backend cannot read `public/locales/common`

Two blockers:

1.  **Build context.** The backend image builds from context `src/backend`
    (`compose.yaml`) with `COPY . /app/` (`src/backend/Dockerfile`).
    `src/frontend/public/locales` is outside that context, so those files are
    in no backend image. Sharing them means moving the backend build context to
    the repository root, which touches every backend/worker/flower build stanza,
    `.dockerignore`, and the CI publish workflow.
2.  **The `common` namespace is generated.** `i18next-cli extract`
    (`src/frontend/i18next.config.ts`) rewrites `common/en-US.json` from a
    static scan of `src/**/*.tsx`. A key added by hand with no `t()` call behind
    it is dropped on the next `make i18n-generate-front`.

### Planned approach

Give the backend its own namespace in the same format and the same pipeline:

-   `src/backend/core/locales/{en-US,fr-FR,nl-NL}.json` — flat natural-key JSON,
    identical in shape to the frontend catalogs (the key *is* the English source
    string, `{{var}}` interpolation). It lives inside the backend build context,
    so it ships in every image with no Dockerfile change.
-   A third entry in `crowdin/config.yml` (source
    `/backend/core/locales/en-US.json`, dest `/backend.json`, translation
    `/backend/core/locales/%locale%.json`), so `make i18n-upload` and
    `make i18n-download` cover it with no new tooling.
-   A small loader, following `core/ai/thread_summarizer.py`
    (`Path(__file__).parent / …`, cached): `t(key, lang, **vars)` doing `{{var}}`
    substitution, falling back to `en-US` then to the key itself.
    `User.language` already stores `en-us` / `fr-fr` / `nl-nl`. The export
    notification lands in a mailbox, which has no language of its own: it
    would follow the language of the user who queued the export.

One caveat: the `_one` / `_many` / `_other` suffixes in the frontend catalogs
are CLDR plural rules (French uses one/many/other, Dutch one/other).
Reimplementing that selection in Python is the awkward part; backend strings can
avoid it by wording counts as `Messages exported: {{count}}` rather than
`{{count}} messages exported`.

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
