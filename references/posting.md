# Connection and posting behavior

The default NOvA base URL is `https://dbweb1.fnal.gov:8443/ECL/nova`.
It was read from the user's `ecl_url.txt`. Configuration precedence is
`--url`, `--url-file`, `ECL_URL`, the working directory's `ecl_url.txt`,
then the bundled URL file.

Use `ecl_access.txt` for the XML API and `ecl_access_web.txt`
only for inspecting forms in the web UI. Both have the username on line 1
and password on line 2. API credentials may instead be a single password
line with `--user` or `ECL_USER`, or JSON with `user` and `password` keys.
There is no built-in account name.
Paths can be set with `--credentials`, `ECL_CREDENTIALS_FILE`,
`--web-credentials`, or `ECL_WEB_CREDENTIALS_FILE`. Credentials are read
at runtime and are not copied into this skill. Do not print their contents.

## Protocol facts established on 2026-10-27

- HTTPS posting worked using the configured API account and MD5 signature headers.
  Entry 257947, "Generic ECL API test", was created and read back.
- Adding the old client's `X-Password` header returned nginx HTTP 400.
  The bundled Python 3 client omits it for both HTTP and HTTPS.
- For query string `args` and exact UTF-8 XML `body`, the signature is
  `md5(args + ':' + password + ':' + body)`. Requests carry `X-User`,
  `X-Signature-Method: md5`, and `X-Signature`. Salt is in the query.
- POST `/E/xml_post` uses `Content-Type: text/xml`. A successful reply is
  plain text such as `Created 257947`; HTTP 200 alone does not confirm creation.
- Read back through `/E/xml_get?e=ID&salt=...`; returned XML does not include
  an entry ID attribute. Check the requested ID and returned metadata/fields.
- Find recent IDs with `ECLConnection.list(form=NAME, limit=10)`. Broad full-text
  searches (`text=...`) timed out; narrow by form/date instead.
- All seven forms have been inspected through authenticated web pages
  `/E/create_entry?f=NAME`. Use `inspect_forms.py` to refresh definitions.
  Web login `ret_url` must be a path such as `/ECL/nova/E/create_entry`.
  An absolute URL produces a malformed redirect in this deployment.
- Some existing web-created multiselect answers export as the literal `,`
  through `xml_get`. This is not evidence that the selected answer was a comma.
  Verification reports these fields as unverified and retains the created ID.

The active `scripts/ECLAPI.py` keeps the supplied classes and familiar
`post`, `list`, `get`, `setValue`, `addImage`, and `addAttachment` methods,
with Python 3 encoding, binary attachments, finite timeouts, and no POST retries.
The original Python 2 file is preserved byte-for-byte at
`assets/legacy/ECLAPI.py`; it is reference material, not the active import.

## Drafts and values

Each generated helper offers `--write-template PATH`, `--input PATH`,
`--list-fields`, `--set 'Exact field name=value'`, and individual
`--field-...` options. `--list-fields` prints labels, help text, and exact choices.
Unknown field names and invalid choices are rejected. Field names remain
case sensitive, including whitespace and existing typos.

Templates have null values to represent missing observations. Null omits a
field; an empty string explicitly supplies a blank text field. Checkboxes
accept JSON booleans or `true`/`false` strings and serialize as `True`/`False`.
Do not populate checkboxes, run numbers, contacts, beam state, or status
answers from template defaults or old entries.

For a multiselect control, these XML scripts accept one exact choice as a
string (or a one-element JSON array). Multiple selections are deliberately
rejected: their XML encoding is not established by the supplied client or
this session. Use the web UI when multiple simultaneous answers are needed.
Scalar form submissions have not been live-tested by posting during skill
creation. Confirm their rendering in the web UI after the first authorized use.

Example draft:

```bash
python3 "$SKILL/scripts/post_expert_contact.py" --write-template contact.json
# Fill the template with actual observations, then:
python3 "$SKILL/scripts/post_expert_contact.py" --input contact.json --output contact.xml
```

Add `--post --credentials ../private/ecl_access.txt` only for an authorized post.
The draft path is optional; without `--output`, XML is printed to stdout.
Use a meaningful subject, category `General` unless requested otherwise, and
the form's predefined tags. `--tag TAG` appends a tag; an explicit JSON `tags`
list replaces the defaults.

`--image 'NAME=screenshots/desktop.png'` and `--attach 'NAME=reports/report.txt'`
add binary attachments. JSON `images` and `attachments` are lists of
`{"name": "...", "path": "..."}` objects; relative paths resolve against
the input JSON's directory. File names are reduced to their basename in XML.
The helpers do not take screenshots or imply that a physical check was done.

## Receipts and uncertain outcomes

Before posting, the helper creates a receipt with an exclusive file creation.
The default path is `.ecl-receipts/<content-hash>.json` in the working directory;
`--receipt PATH` chooses an explicit path. Reuse that receipt for retries.
Receipts contain an ID, target, content hash, outcome, and verification result;
they contain neither credentials nor the full entry body.

An existing receipt in `created` state triggers GET-only verification and
does not POST again. A `pending`, `uncertain`, or `rejected` receipt stops
another POST. Check the logbook and receipt before deciding the next step.
A timeout, connection loss, server error, or unrecognized success body can
leave creation uncertain. Do not automatically switch hosts or retry such a
request. A deliberately separate identical entry needs a distinct receipt
path and authorization for that additional entry.

`post_entry.py --get ID --credentials PATH` retrieves raw entry XML without
posting. A created ID is recorded before read-back so a failed verification
never causes a repeat POST. Report mismatched or unverified fields to the user.

## HTTP and SSH fallback

HTTPS works locally; SSH is not normally necessary. If the user requests an
HTTP fallback, execute the helpers on the requested Fermilab host using
Python 3, a verified HTTP endpoint, and `--allow-http`. Do not change a TLS
port to plain HTTP or guess another experiment's endpoint.

In this session, SSH to the requested Fermilab host worked with GSSAPI.
`http://dbweb1.fnal.gov:8080` and port 80 refused connections from that host;
plain HTTP on port 8443 returned "The plain HTTP request was sent to HTTPS port".
Those failures are not a working HTTP endpoint to hard-code.

If the local SSH client rejects a system configuration file because of its
permissions, use `ssh -F none` with the required authentication options and
configure the SSH user and host externally. For example:

```bash
ssh -F none -o GSSAPIAuthentication=yes -o GSSAPIDelegateCredentials=yes \
  -l "$ECL_SSH_USER" "$ECL_SSH_HOST" ...
```

Preserve host verification. Transfer only code
and the intended entry; provide credentials through a protected file or encrypted
SSH stdin, never command arguments or shell history.
