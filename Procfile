web: bin/scalingo_run_web
worker: celery -A messages.celery_app worker --task-events --beat -l DEBUG -c $CELERY_CONCURRENCY
postdeploy: python manage.py migrate