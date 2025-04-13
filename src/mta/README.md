# ST Messages MTA

The MTA is in charge of receiving emails from the Internet and pushing them to the app. It also sends outgoing emails.

This MTA container is based on standard technologies such as Postfix, and is entirely stateless. It should be entirely configurable from env vars.

It is battle-tested with a complete Python test suite.

After receiving an email through SMTP, it does, in order:
 - Check if the mailbox exist with an API call, through a python script
 - Push it through a Python script to a HTTP API acting as MDA