from django_dramatiq.management.commands.rundramatiq import (
    Command as RunDramatiqCommand,
)


class Command(RunDramatiqCommand):
    def discover_tasks_modules(self):
        tasks_modules = super().discover_tasks_modules()
        tasks_modules[0] = "core.worker_setup"
        return tasks_modules
