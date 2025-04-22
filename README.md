# Messages

Messages is the all-in-one collaborative inbox for [La Suite territoriale](https://suiteterritoriale.anct.gouv.fr/).

It is built on top of [Django Rest
Framework](https://www.django-rest-framework.org/) and [Next.js](https://nextjs.org/).

## Getting started

### Prerequisite

Make sure you have a recent version of Docker and [Docker
Compose](https://docs.docker.com/compose/install) installed on your machine:

```bash
$ docker -v
  Docker version 27.5.1, build 9f9e405

$ docker compose version
  Docker Compose version v2.32.4
```

> ⚠️ You may need to run the following commands with `sudo` but this can be
> avoided by assigning your user to the `docker` group.

### Bootstrap project

The easiest way to start working on the project is to use GNU Make:

```bash
$ make bootstrap
```

This command builds the `app-dev` container, installs dependencies, performs
database migrations and compile translations. It's a good idea to use this
command each time you are pulling code from the project repository to avoid
dependency-related or migration-related issues.

Your Docker services should now be up and running! 🎉

Note that if you need to run them afterward, you can use the eponym Make rule:

```bash
$ make run
```

You can check all available Make rules using:

```bash
$ make help
```

### Django admin

You can access the Django admin site at
[http://localhost:8071/admin](http://localhost:8071/admin).

You first need to create a superuser account:

```bash
$ make superuser
```

You can then login with email `admin@example.com` and password `admin`.


### Run frontend

Run the front with:

```bash
$ make run-with-frontend
```

Then access [http://localhost:3000](http://localhost:3000) with :
user: user{1,2,3}
password: user{1,2,3}

### OpenAPI client

The frontend API client is generated with
[Orval](https://orval.dev/). It consumes the OpenAPI schema generated from the backend through 
[drf-spectacular](https://drf-spectacular.readthedocs.io/en/latest/).

The JSON OpenAPI schema is located in
`src/backend/core/api/openapi.json`.

To update the schema then the frontend API client, run:

```bash
$ make api-update
```

You can also generate the schema only with:

```bash
$ make backend-api-update
```

And the frontend API client only with:

```bash
$ make frontend-api-update
```





## Contributing

This project is intended to be community-driven, so please, do not hesitate to
get in touch if you have any question related to our implementation or design
decisions.

## License

This work is released under the MIT License (see [LICENSE](./LICENSE)).
