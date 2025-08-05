# Model Custom Attributes

## Purpose

The `User` and `MailDomain` models support storing additional data via a `custom_attributes` JSON field.
If an identity provider is configured, these attributes are also synchronized with it.

> **🗒️ Note**
> This feature allows you to extend the core models without altering the database schema.

---

## Usage

For each model, the structure of the `custom_attributes` data can be defined via
[environment variables](./env.md#model-custom-attributes-schema).
This enables customization of your Messages instance **without forking the project**.

The value must be a **valid JSON Schema** string following the
[2020-12 specification](https://json-schema.org/draft/2020-12).

> **💡 Tip**
> Before deploying your application, lint your JSON Schema to ensure it is correctly formatted.
> [Several tools are available](https://json-schema.org/tools?query=&sortBy=name&sortOrder=ascending&groupBy=toolingTypes&licenses=&languages=&drafts=&toolingTypes=linter,validator&environments=Web+(Online)&showObsolete=false&supportsBowtie=false).

> **⚠️ Warning**
> Altering schemas could require to you to migrate your data! Modify it with caution.

---

## Schema Structure

**Custom attributes must be an object with only primitive properties
(string, number, integer or boolean).**

Example of a valid JSON Schema:

- `job_title`: a string with a minimum length of 3 characters.
- `is_elected`: a boolean that defaults to `false`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/suitenumerique/messages/schemas/custom-fields/user",
  "type": "object",
  "title": "User custom fields",
  "additionalProperties": false,
  "properties": {
    "job_title": {
      "type": "string",
      "title": "Job title",
      "default": "",
      "description": "The job name of the user",
      "minLength": 3,
      "x-i18n": {
        "title": {
          "fr": "Fonction",
          "en": "Job title"
        },
        "description": {
          "fr": "Le nom de la fonction de l'utilisateur",
          "en": "The job name of the user"
        }
      }
    },
    "is_elected": {
      "type": "boolean",
      "title": "Is elected",
      "default": false,
      "description": "Whether the user is elected",
      "x-i18n": {
        "title": {
          "fr": "Est élu",
          "en": "Is elected"
        },
        "description": {
          "fr": "Indique si l'utilisateur est élu"
        }
      }
    }
  },
  "required": []
}
```

---

### Internationalization

If your application is not internationalized, you can skip this section.
Providing a `title` and `description` for each property is sufficient.

In the example above, we use a
[custom annotation](https://json-schema.org/blog/posts/custom-annotations-will-continue#too-long-read-anyway) named `x-i18n`.
This property allows defining localized `title` and `description` values for each property in the supported languages.

> **🗒️ Note**
> The `x-i18n` object is optional. If omitted, the values in `title` and `description` will be used as-is.

---

## Data Validation

The custom attributes schema is used to:

- Render input fields and validate data on the **frontend**.
- Validate data on the **backend**.

---

### Frontend

- **Schema generation**: We use [zod-from-json-schema](https://github.com/glideapps/zod-from-json-schema) to generate a Zod schema.
  This library provides broad (but partial) support for the 2020-12 specification.

- **Form rendering**: The custom component `RhfJsonSchemaField` chooses the correct input to render based on the property `type`.

- **Form state**: Managed by [React Hook Form](https://react-hook-form.com/), in line with other forms in the application.

> **⚠️ Warning**
> Because `zod-from-json-schema` support for 2020-12 is partial, certain advanced JSON Schema features may not work as expected on the frontend.
> Always test your schema in the UI before deploying.

---

### Backend

We use the [jsonschema](https://github.com/python-jsonschema/jsonschema) package for validation.
It provides **full support** for the 2020-12 specification, ensuring data integrity even if frontend validation is bypassed.
