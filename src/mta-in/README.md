# ST Messages MTA inbound

The MTA is in charge of receiving emails from the Internet and pushing them to the MDA and ultimately the users.

It only deals with inbound email but it also may send bounces.

This MTA container is based on standard technologies such as Postfix, and is entirely stateless. It is entirely configurable from env vars.

It is battle-tested with a complete Python test suite.

After receiving an email through SMTP, it does, in order:
 - Check if the mailbox exist with an REST API call to `{env.MDA_API_BASE_URL}/check-recipients`, through a Python service
 - Push it through a Python script to an API `{env.MDA_API_BASE_URL}/inbound-email` in the MDA

These API calls are secured by a JWT token, using a shared secret `env.MDA_API_SECRET`.