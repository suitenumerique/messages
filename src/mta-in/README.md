# ST Messages MTA inbound

The MTA is in charge of receiving emails from the Internet and pushing them to the MDA and ultimately the users.

It only deals with inbound email and won't even send bounces by itself.

This MTA container is based on standard technologies such as Postfix with a custom Python milter, and is entirely stateless. It is entirely configurable from env vars.

It is battle-tested with a complete Python test suite.

After receiving an email through SMTP, it processes each message synchronously during the SMTP session using a custom Postfix milter that:
 - Validates each recipient with a REST API call to `{env.MDA_API_BASE_URL}/check-recipients` during the RCPT TO command
 - Delivers the complete message via REST API call to `{env.MDA_API_BASE_URL}/inbound-email` during the DATA command
 - Either accepts (discards from queue) or rejects the SMTP session based on delivery results

This architecture ensures true synchronous delivery - delivery failures cause immediate SMTP session rejection, and successful deliveries prevent the message from entering the Postfix queue.

The API calls are secured by a JWT token, using a shared secret `env.MDA_API_SECRET`.

To run the tests, go to the repository root and do:

```
make mta-in-test
```

You should also run lint before commit:

```
make mta-in-lint
```