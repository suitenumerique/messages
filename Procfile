web: deploy/paas/buildpack_start.sh
workerall: python worker.py
workerimports: python worker.py --concurrency=1 --queues=imports --disable-scheduler
workerreindex: python worker.py --concurrency=2 --queues=reindex --disable-scheduler
workerrest: python worker.py --concurrency=4 --exclude=imports,reindex
postdeploy: python manage.py migrate
