# START THE PROJECT
```shell
make bootstrap
```
or
```shell
make start
```

# Generate the emails

_In the shell._
```shell
make generate_emails
```
add files to the private collection
```shell
make private_collection
```
add every Docs document to the private collection (name = title, content = Markdown).
A document already imported with the same title is replaced.
```shell
make private_collection_docs                      # asks the Docs email and password
DOCS_EMAIL=user1@example.local make private_collection_docs ARGS=--dry-run   # list only
```