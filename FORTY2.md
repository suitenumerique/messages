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
Some emails come with attachments (PDF, scanned images, text, Word) stored in
`src/backend/core/data/attachments/`: in the JSON file, add
`"attachments": [{"path": "attachments/file.pdf"}]` to an email or to a message
of a conversation (path relative to the JSON file). All documents are fictitious.

The AI draft reads the citizen's attachments (text files directly, PDF, images
and Word documents through the Albert OCR model `AI_OCR_MODEL`).
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