"""
Management command to run arbitrary Celery tasks synchronously.

This command provides a Django interface to run Celery tasks with the same
CLI flags as the main Celery CLI, but executes them synchronously instead
of queuing them as background tasks.
"""

import importlib
import json
import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Run arbitrary Celery tasks synchronously."""

    help = """
    Run arbitrary Celery tasks synchronously.
    
    Examples:
        python manage.py run_task fetch_service_metrics
        python manage.py run_task fetch_metrics_for_service --pargs '["123e4567-e89b-12d3-a456-426614174000"]'
        python manage.py run_task fetch_service_metrics --kwargs '{"debug": true}'
        python manage.py run_task other_app.tasks.some_task
    """

    def add_arguments(self, parser):
        """Add command line arguments."""
        parser.add_argument("task_name", help="Name of the Celery task to run")

        # Task execution options
        parser.add_argument(
            "--pargs", type=str, help="Positional arguments as JSON string for the task"
        )

        parser.add_argument(
            "--kwargs", type=str, help="Keyword arguments as JSON string for the task"
        )

        # Output options
        parser.add_argument("--json", action="store_true", help="Output result as JSON")

    def handle(self, *args, **options):
        """Execute the command."""
        task_name = options["task_name"]

        # Parse keyword arguments
        kwargs = {}
        if options["kwargs"]:
            try:
                kwargs = json.loads(options["kwargs"])
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON in --kwargs: {e}") from e

        # Parse positional arguments
        task_args = []
        if options["pargs"]:
            try:
                task_args = json.loads(options["pargs"])
            except json.JSONDecodeError as e:
                raise CommandError(f"Invalid JSON in --pargs: {e}") from e

        # Get task function
        task_func = self._get_task_function(task_name)
        if not task_func:
            raise CommandError(f"Task '{task_name}' not found")

        self.stdout.write(f"Running task: {task_name}")
        self.stdout.write(f"Arguments: {task_args}")
        self.stdout.write(f"Keyword arguments: {kwargs}")
        self.stdout.write("")

        try:
            # Execute task synchronously
            result = task_func(*task_args, **kwargs)

            # Output result
            if options["json"]:
                self.stdout.write(json.dumps(result, indent=2, default=str))
            else:
                self.stdout.write(self.style.SUCCESS("Task completed successfully"))
                self.stdout.write(f"Result: {result}")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Task failed: {e}"))
            raise CommandError(f"Task execution failed: {e}") from e

    def _get_task_function(self, task_name: str):
        """Get the task function by name using dynamic imports."""
        try:
            # Try to import from core.tasks first
            tasks_module = importlib.import_module("core.tasks")
            if hasattr(tasks_module, task_name):
                return getattr(tasks_module, task_name)

            # If not found, try to import from the full module path
            # This allows running tasks from other apps too
            if "." in task_name:
                module_path, func_name = task_name.rsplit(".", 1)
                module = importlib.import_module(module_path)
                return getattr(module, func_name)

            self.stdout.write(
                self.style.WARNING(f"Task '{task_name}' not found in core.tasks module")
            )
            return None

        except ImportError as e:
            self.stdout.write(
                self.style.ERROR(f"Failed to import task '{task_name}': {e}")
            )
            return None
        except AttributeError as e:
            self.stdout.write(
                self.style.ERROR(f"Task '{task_name}' not found in module: {e}")
            )
            return None
