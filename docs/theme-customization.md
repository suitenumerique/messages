# Theme Customization

You can customize the theme of the frontend by setting the `NEXT_PUBLIC_THEME_CONFIG` environment variable.

The theme configuration is a JSON object that contains the following properties:

- `theme`: The theme to use. Possible values are: `white-label`, `anct`, `dsfr`.
Each theme are available in two variants: `light` and `dark`.
- `terms_of_service_url`: The terms of service URL that will be displayed in the user menu.
- `footer`: Customize logo and links in the footer.
- `with_proconnect`: Show the ProConnect button on the home page (default: `true`).
Set it to `false` to display a generic "Sign in" button instead, e.g. when your
OIDC provider is not ProConnect.

Example:

```json
{"theme": "white-label", "with_proconnect": false}
```

## About the footer

The footer is displayed on the home page and can be customized. If no footer configuration is provided,
the footer will not be displayed. The configuration object corresponds to the props `FooterProps` defined in
 the [UI Kit Footer Component](https://github.com/suitenumerique/ui-kit/blob/main/src/components/footer/Footer.tsx).

## Theme assets

Each theme has its own assets. The assets must be stored in the `public/images/<theme>/` directory.
