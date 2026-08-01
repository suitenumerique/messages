web: deploy/paas/scalingo_run_web
# The periodic scheduler runs inside any worker started without
# --disable-scheduler. It holds a Redis lock, so only one is ever live and the
# others stand by to take over — leaving it on for more than one process type
# is what makes the schedule survive a single dyno going away.
workerall: python worker.py
# One import at a time, in dispatch order: an import run is long and sequential.
workerimports: python worker.py --concurrency=1 --queues=imports --disable-scheduler
workerreindex: python worker.py --concurrency=2 --queues=reindex --disable-scheduler
# The hourly blob sweeps (GC and offload to object storage) run for up to 55
# minutes each. Consumed on their own so nothing short is ever reserved behind
# them — see LONG_RUNNING_QUEUES in worker.py.
workerblobs: python worker.py --concurrency=1 --queues=blobs --disable-scheduler
workerrest: python worker.py --concurrency=4 --exclude=imports,reindex,blobs
postdeploy: python manage.py migrate
