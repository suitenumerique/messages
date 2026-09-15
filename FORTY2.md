# START THE PROJECT
```shell
make bootstrap
```
or
```shell
make start
```

# Generate the emails

_In the backend docker._
```shell
python manage.py seed_citizen_email --mailbox user1@example.local --data-file core/data/generated_emails.json
```