<p align="center">
  <a href="https://github.com/suitenumerique/messages">
    <img alt="Messages" src="/docs/assets/readme-banner.png" width="100%" />
  </a>
</p>
<p align="center">
  <a href="https://github.com/suitenumerique/messages/stargazers/">
    <img src="https://img.shields.io/github/stars/suitenumerique/messages" alt="">
  </a>
  <a href='https://github.com/suitenumerique/messages/blob/main/CONTRIBUTING.md'><img alt='PRs Welcome' src='https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=shields'/></a>
  <img alt="GitHub commit activity" src="https://img.shields.io/github/commit-activity/m/suitenumerique/messages"/>
  <img alt="GitHub closed issues" src="https://img.shields.io/github/issues-closed/suitenumerique/messages"/>
  <a href="https://github.com/suitenumerique/messages/blob/main/LICENSE">
    <img alt="MIT License" src="https://img.shields.io/github/license/suitenumerique/messages"/>
  </a>
</p>
<p align="center">
  <a href="https://matrix.to/#/#messages-official:matrix.org">
    Chat on Matrix
  </a> - <a href="/docs/">
    Documentation
  </a> - <a href="https://github.com/orgs/suitenumerique/projects/4">
    Roadmap
  </a> - <a href="#getting-started-">
    Getting started
  </a> - <a href="mailto:contact@suite.anct.gouv.fr">
    Reach out
  </a>
</p>

# Messages : Collaborative Inbox

Messages is the all-in-one collaborative inbox for [La Suite territoriale](https://suiteterritoriale.anct.gouv.fr/).

<img src="/docs/assets/readme-app.webp" alt="Messages screenshot" width="100%" align="center"/>

## Why use Messages ❓

Messages is a full communication platform enabling teams to collaborate on emails through shared or personal mailboxes.

It features a [MTA](https://en.wikipedia.org/wiki/Message_transfer_agent) based on [Postfix](https://www.postfix.org/), a custom [MDA](https://en.wikipedia.org/wiki/Message_delivery_agent) built on top of [Django Rest Framework](https://www.django-rest-framework.org/) and a frontend using [Next.js](https://nextjs.org/) and [BlockNote.js](https://www.blocknotejs.org/).

### Familiar messaging features
* 📝 Receive, draft and send emails.
* 🧵 Smart threading
* 📎 Upload and download attachments. Also works with [Drive](https://github.com/suitenumerique/drive)!
* 📩 Import emails from MBOX or IMAP
* 🔎 Full-text search with advanced filters
* ⏳️ Asynchronous, pluggable email processing (antispam, antivirus, ...)
* 🤖 AI Summaries, AI message composer, AI auto-labelling
* 🎨 Embeddable widgets for feedback

### Collaboration at the core
* 👥 Share any inbox with multiple users
* 🧶 Share threads with other users
* (soon) 🕶 Private messages between users
* (soon) 💎 Realtime text editing
* (soon) 👉 Assign threads to specific users

### Based on standards
* 🔑 OpenID Connect for all user accounts. Plug any identity provider, including Keycloak.
* 📬 SMTP in and out.
* ❌ No POP3 or IMAP client support, by design. We're building for the future, not the (unsecure) past!
* ✅ JMAP-inspired data model. Full support could be added.

### Self-host
* 🚀 Messages is designed to be installed on the cloud or on your own servers.
* 🛠️ Configuration through environment variables for most settings

<img src="/docs/assets/architecture-high-level.png" alt="Messages architecture" width="100%" align="center"/>

## Getting started 🔧

### Prerequisite

To test Messages on your own machine, you only need a recent version of Docker and [Docker
Compose](https://docs.docker.com/compose/install):

```shellscript
$ docker -v
  Docker version 27.5.1, build 9f9e405

$ docker compose version
  Docker Compose version v2.32.4
```

> ⚠️ You may need to run the following commands with `sudo` but this can be
> avoided by assigning your user to the `docker` group.

### Project bootstrap

The easiest way to start working on the project is to use [GNU Make](https://www.gnu.org/software/make/):

```shellscript
$ make bootstrap
```

This command builds all required containers, installs dependencies, performs
database migrations and compiles translations. Later it's a good idea to run
`make update` each time you are pulling code from the project repository to avoid
dependency-related or migration-related issues.

Your Docker services should now be up and running 🎉

You can access the project by going to <http://localhost:8900>.

You will be prompted to log in. The default credentials are:

```
email: user{1,2,3}@example.local
password: user{1,2,3}
```

This means you can use `user1@example.local / user1` for instance and switch users later to test collaboration.

In your development workflow, the main commands you should use are:

```
# Stop all containers
$ make stop

# Start all containers, without full bootstrap
$ make start

# View all available commands
$ make help
```

### Development Services

When running the project, the following services are available:

| Service | URL / Port | Description | Credentials |
|---------|------------|-------------|------------|
| **Frontend** | [http://localhost:8900](http://localhost:8900) | Main Messages frontend | `user1@example.local` / `user1` |
| **Backend API** | [http://localhost:8901](http://localhost:8901) | Django [REST API](http://localhost:8901/api/v1.0/) and [Admin](http://localhost:8901/admin/) | `admin@admin.local` / `admin` |
| **Keycloak** | [http://localhost:8902](http://localhost:8902) | Identity provider admin | `admin` / `admin` |
| **Celery UI** | [http://localhost:8903](http://localhost:8903) | Task queue monitoring | No auth required |
| **Mailcatcher** | [http://localhost:8904](http://localhost:8904) | Email testing interface | No auth required |
| **MTA-in (SMTP)** | 8910 | Incoming email server | No auth required |
| **MTA-out (SMTP)** | 8911 | Outgoing email server | `user` / `pass` |
| **PostgreSQL** | 8912 | Database server | `user` / `pass` |
| **Redis** | 8913 | Cache and message broker | No auth required |
| **OpenSearch** | 8914 | Search engine | No auth required |
| **OpenSearch PA** | 8915 | Performance analyzer | No auth required |
| **SOCKS Proxy** | 8916 | SOCKS5 proxy | `user1` / `pwd1` |
| **Mailcatcher (SMTP)** | 8917 | SMTP server | No auth required |
| **MPA (Rspamd)** | 8918 | Spam filtering service | `password` |


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
$ make back-api-update
```

And the frontend API client only with:

```bash
$ make front-api-update
```

### Sending test emails 📨

There are a couple ways of testing the email infrastructure locally.

These examples use [swaks](https://www.jetmore.org/john/code/swaks/), a simple command-line SMTP client.

```
# First, make sure services are running
make start

# Send a test message to the MTA-in, which will relay it to the Django MDA.
# The domain must be MESSAGES_TESTDOMAIN (default is example.local) if you want the mailbox created automatically.
# You can then read it on the frontend at http://localhost:8900/ (login as user1/user1) and reply to it there.
# The replies will then be sent to the mailcatcher on http://localhost:8904/
swaks --to=user1@example.local --server localhost:8910

# Send a test message to the mailcatcher, then read it on http://localhost:8904/
swaks --to=user1@example.local --server localhost:8917

# Send a test message to the MTA-out, which will then relay it to mailcatcher on http://localhost:8904/
swaks -tls --to=test@example.external --server localhost:8911 --auth-user user --auth-password=pass

# You can also send emails using Messages itself instead of the frontend
make back-shell
MTA_OUT_MODE=relay MTA_OUT_RELAY_HOST=mailcatcher:1025 python manage.py send_mail --to=user1@example.local --subject="Test" --body="Hello World"

```

> ⚠️ Most residential ISPs block the outgoing port 25, so you might not be able to send emails to outside
> servers from your localhost. This is why the mailcatcher is so useful locally.

## Feedback 🙋‍♂️🙋‍♀️

We'd love to hear your thoughts, and hear about your experiments, so come and say hi on [Matrix](https://matrix.to/#/#messages-official:matrix.org).


## License 📝

This work is released under the MIT License (see [LICENSE](https://github.com/suitenumerique/messages/blob/main/LICENSE)).

While Messages is a public-driven initiative, our license choice is an invitation for private sector actors to use, sell and contribute to the project.


## Contributing 🙌


This project is intended to be community-driven, so please, do not hesitate to [get in touch](https://matrix.to/#/#messages-official:matrix.org) if you have any question related to our implementation or design decisions.

We also have a [public roadmap](https://github.com/orgs/suitenumerique/projects/4).

You can help us with translations on [Crowdin](https://crowdin.com/project/lasuite-messages).

If you intend to make pull requests, see [CONTRIBUTING](https://github.com/suitenumerique/messages/blob/main/CONTRIBUTING.md) for guidelines.


## Gov ❤️ open source

Messages is currently led by the French [ANCT](https://anct.gouv.fr/) for use in [La Suite territoriale](https://suiteterritoriale.anct.gouv.fr/).

We are welcoming new partners and contributors to join us in this effort! So please [get in touch](mailto:contact@suite.anct.gouv.fr) if you want to help!
