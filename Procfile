web: bin/scalingo_run_web
worker_all: python worker.py
worker_imports: python worker.py --concurrency=1 --queues=imports --disable-scheduler
worker_reindex: python worker.py --concurrency=2 --queues=reindex --disable-scheduler
worker_rest: python worker.py --concurrency=4 --exclude=imports,reindex
postdeploy: python manage.py migrate
