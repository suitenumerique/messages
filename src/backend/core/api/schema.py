"""drf-spectacular hooks."""

from core.enums import APP_PASSWORD_SCOPES


def add_app_password_scopes(result, generator, **kwargs):  # pylint: disable=unused-argument
    """Publish the scopes an app password may hold as the ``AppPasswordScopeEnum`` schema.

    ``Channel.settings`` is free-form JSON, so no serializer exposes them; the
    frontend builds its role presets from this enum instead of hardcoding them.
    """
    result.setdefault("components", {}).setdefault("schemas", {})[
        "AppPasswordScopeEnum"
    ] = {
        "type": "string",
        "enum": sorted(APP_PASSWORD_SCOPES),
        "description": "A scope an app password (client-bridge channel) may hold.",
    }
    return result
