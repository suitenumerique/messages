"""Custom pylint checkers for the Messages backend."""

from astroid import nodes
from pylint.checkers import BaseChecker
from pylint.lint import PyLinter


class NoGetAttrSettingsChecker(BaseChecker):
    """Forbid ``getattr(settings, ...)`` — use ``settings.SETTING`` directly.

    Django settings defined with django-configurations are real class
    attributes. Using ``getattr`` with a default silently hides missing
    settings and bypasses the configuration layer.
    """

    name = "no-getattr-settings"
    msgs = {
        "C9001": (
            "Do not use getattr() on Django settings. "
            "Access settings.%s directly instead.",
            "getattr-on-settings",
            "getattr(settings, ...) bypasses django-configurations and "
            "silently falls back to a hardcoded default. Declare the "
            "setting in the Configuration class and access it directly.",
        ),
    }

    def visit_call(self, node: nodes.Call) -> None:  # pylint: disable=missing-function-docstring
        # Match getattr(settings, "SOMETHING", ...)
        if not isinstance(node.func, nodes.Name) or node.func.name != "getattr":
            return
        if len(node.args) < 2:
            return

        obj = node.args[0]
        attr = node.args[1]

        # Check if the first argument is `settings` or `django_settings`
        if not isinstance(obj, nodes.Name):
            return
        if obj.name not in ("settings", "django_settings"):
            return

        # Extract the setting name for the message
        setting_name = attr.value if isinstance(attr, nodes.Const) else "..."
        self.add_message("getattr-on-settings", node=node, args=(setting_name,))


def register(linter: PyLinter) -> None:  # pylint: disable=missing-function-docstring
    linter.register_checker(NoGetAttrSettingsChecker(linter))
