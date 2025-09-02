"""Management command to run the self-check functionality."""

import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

from core.mda.selfcheck import run_selfcheck


class SelfCheckMetricsBase(object):
    """Dummy class that should be subclassed for different metrics backends."""

    def mark_start(self):
        pass

    def mark_end(self):
        pass

    def mark_failure(self):
        pass

    def mark_success(self):
        pass

    def write_send_time(self, send_time):
        pass

    def write_reception_time(self, reception_time):
        pass

    def send_metrics(self):
        pass


class SelfCheckPrometheusMetrics(SelfCheckMetricsBase):
    """Prometheus metrics for the selfcheck process."""

    def __init__(self):
        prefix = settings.MESSAGES_SELFCHECK_PROMETHEUS_METRICS_PREFIX
        if settings.MESSAGES_SELFCHECK_PROMETHEUS_METRICS_PUSHGATEWAY_URL is None:
            raise ValueError("Prometheus push gateway URL is not set.")
        self.registry = CollectorRegistry()
        self.start_time = Gauge(
            f"{prefix}selfcheck_start_time",
            "Start timestamp of the self check",
            registry=self.registry,
        )
        self.end_time = Gauge(
            f"{prefix}selfcheck_end_time",
            "End timestamp of the self check",
            registry=self.registry,
        )
        self.success = Gauge(
            f"{prefix}selfcheck_success",
            "Success of the self check",
            registry=self.registry,
        )
        self.send_duration = Gauge(
            f"{prefix}selfcheck_send_duration_seconds",
            "Send duration of the self check",
            registry=self.registry,
        )
        self.reception_duration = Gauge(
            f"{prefix}selfcheck_reception_duration_seconds",
            "Reception duration of the self check",
            registry=self.registry,
        )

    def mark_start(self):
        self.start_time.set_to_current_time()

    def mark_end(self):
        self.end_time.set_to_current_time()

    def mark_failure(self):
        self.success.set(0)

    def mark_success(self):
        self.success.set(1)

    def write_send_time(self, send_time):
        self.send_duration.set(send_time)

    def write_reception_time(self, reception_time):
        self.reception_duration.set(reception_time)

    def send_metrics(self):
        return push_to_gateway(
            settings.MESSAGES_SELFCHECK_PROMETHEUS_METRICS_PUSHGATEWAY_URL,
            job="selfcheck",
            registry=self.registry,
        )


class Command(BaseCommand):
    """Run a selfcheck of the mail delivery system."""

    help = "Run an end-to-end selfcheck of the mail delivery system"

    def add_arguments(self, parser):
        """Add command arguments."""
        pass

    def handle(self, *args, **options):
        """Execute the command."""

        metrics = (
            SelfCheckPrometheusMetrics()
            if settings.MESSAGES_SELFCHECK_PROMETHEUS_METRICS_ENABLED
            else SelfCheckMetricsBase()
        )

        self.stdout.write("Starting selfcheck...")
        self.stdout.write(f"FROM: {settings.MESSAGES_SELFCHECK_FROM}")
        self.stdout.write(f"TO: {settings.MESSAGES_SELFCHECK_TO}")
        self.stdout.write(f"SECRET: {settings.MESSAGES_SELFCHECK_SECRET}")
        self.stdout.write("")

        metrics.mark_start()

        # Run the selfcheck
        result = run_selfcheck()

        metrics.mark_end()

        # Display results
        if result["success"]:
            self.stdout.write(self.style.SUCCESS("✓ Selfcheck completed successfully!"))
            metrics.mark_success()
            self.stdout.write("")
            self.stdout.write("Timings:")
            if result["send_time"] is not None:
                metrics.write_send_time(result["send_time"])
                self.stdout.write(f"  Send time: {result['send_time']:.2f}s")
            if result["reception_time"] is not None:
                metrics.write_reception_time(result["reception_time"])
                self.stdout.write(f"  Reception time: {result['reception_time']:.2f}s")
            metrics.send_metrics()
        else:
            self.stdout.write(
                self.style.ERROR(f"✗ Selfcheck failed: {result['error']}")
            )
            metrics.mark_failure()
            metrics.send_metrics()
            sys.exit(1)
