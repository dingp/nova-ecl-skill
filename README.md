# NOvA ECL posting skill

Draft and post entries to Fermilab's NOvA Electronic Collaboration Logbook
using Python 3. Includes a Codex skill, a Python 3 API client, and scripts and
editable templates for seven predefined forms.

Default logbook: <https://dbweb1.fnal.gov:8443/ECL/nova>.
The API username is read from your credential file or explicit configuration.

## Setup

The scripts use the Python standard library and were tested with Python 3.9.
No Python packages need installing.

Clone this repository, replacing `REPOSITORY_URL` with its URL:

```bash
git clone REPOSITORY_URL nova-ecl
```

Place the checkout in your configured Codex skills directory under the name
`nova-ecl`. Start a session with the skill available, then invoke `$nova-ecl`, for
example: "Use $nova-ecl to draft an expert-contact entry from these notes."
You can also run every script directly without Codex. The commands below
assume your working directory is this repository.

Keep credentials outside the checkout. The existing local credential files
can be used directly:

```bash
export ECL_CREDENTIALS_FILE=../private/ecl_access.txt
export ECL_WEB_CREDENTIALS_FILE=../private/ecl_access_web.txt
```

Both files contain a username on line 1 and password on line 2. Use the API
account for posting and the separate web account for inspecting form definitions.
A password-only API file requires `--user` or `ECL_USER`. There is no built-in
username. Drafts omit the author unless `--user` or `ECL_USER` supplies one.

Override these paths with `--credentials PATH` or `--web-credentials PATH`.
Configure the logbook using `--url URL`, `--url-file PATH`, `ECL_URL`, or
`ecl_url.txt` in the working directory. The bundled URL is the fallback.
Passwords are never command-line arguments or included in this repository.

## Generic entry

This produces a draft only:

```bash
python3 scripts/post_entry.py \
  --subject 'Example draft' \
  --text 'Draft for review.' \
  --output draft.xml
```

Use `--text-file PATH` for a longer entry. Add `--post` only when the content
is ready and posting is authorized. Without `--post`, the scripts create XML
locally; they do not submit anything.

## Predefined forms

| Form | Fields | Script |
| --- | ---: | --- |
| `2026_Control-Room_Checklist` | 26 | `scripts/post_control_room_checklist.py` |
| `2026_Stable_Running_Checklist` | 42 | `scripts/post_stable_running_checklist.py` |
| `2025_Expert-contact` | 5 | `scripts/post_expert_contact.py` |
| `2026_Start_Shift` | 19 | `scripts/post_start_shift.py` |
| `2026_Shift-check-in` | 8 | `scripts/post_shift_check_in.py` |
| `2026_Shift-check-out` | 3 | `scripts/post_shift_check_out.py` |
| `2026_Shift-summary` | 10 | `scripts/post_shift_summary.py` |

All seven forms were inspected through the authenticated web UI on
2026-10-27. Their exact field names, labels, help, nonpersonal choices, and HTML control
types are recorded in [references/forms.json](references/forms.json).
Templates are under [assets/templates](assets/templates).

Generate an editable template and inspect the field definitions:

```bash
python3 scripts/post_shift_check_in.py --write-template check-in.json
python3 scripts/post_shift_check_in.py --list-fields
```

Fill the JSON with a subject and actual observations, then preview:

```bash
python3 scripts/post_shift_check_in.py --input check-in.json --output check-in.xml
```

When authorized to submit that entry:

```bash
python3 scripts/post_shift_check_in.py --input check-in.json --post
```

Each helper also supports `--set 'Exact field name=value'` and individual
`--field-...` options; run `--help` for the generated flags. Invalid names and
choices are rejected. Significant spellings such as `cr06_windows _present`
and `fd_onmom_timestamp` are preserved.

Template values start as `null` so no check is automatically marked complete.
Null omits a field, an empty string supplies a blank text field, and checkbox
values are JSON booleans. Do not fill status answers from old entries.

The XML helpers accept one exact choice per multiselect field. Multiple
simultaneous selections are rejected because their XML encoding has not been
verified; use the web UI for those cases. Some web-created multiselect answers
export as `,` through the XML API. Read-back reports those fields as unverified.
Form submissions have been validated offline, without posting additional live
checklists. Check the rendering after the first authorized use of a form.

## Attachments and verification

Add images or files to a draft or authorized post:

```bash
python3 scripts/post_control_room_checklist.py --input control-room.json \
  --image 'FD desktop=screenshots/fd-desktop.png' \
  --attach 'Notes=notes.txt' --output control-room.xml
```

JSON templates also accept `images` and `attachments` lists containing
`{"name": "...", "path": "..."}` objects. Relative JSON paths are resolved
against the template's directory. Binary file content is base64 encoded.

Posting creates a local receipt in `.ecl-receipts/`, records the returned
entry ID, and reads the entry back. Reusing a successful receipt verifies with
GET only, preventing an accidental duplicate. `--receipt PATH` chooses a
specific receipt file. An uncertain or rejected outcome stops another POST;
inspect the receipt and logbook before retrying.

Read an existing entry without posting:

```bash
python3 scripts/post_entry.py --get 257947 --output entry.xml
```

HTTPS signature authentication worked without the legacy `X-Password` header;
including that header returned HTTP 400 on this server. The client keeps TLS
verification enabled and never automatically retries a POST. HTTP requires
`--allow-http` and a working HTTP endpoint. See
[references/posting.md](references/posting.md) for recovery and SSH details.

## Refresh definitions and test

Read the live form definitions using the separate web credentials:

```bash
python3 scripts/inspect_forms.py --output refreshed-forms.json
```

The inspector does not post entries or save session cookies. Reconcile a
changed schema with the bundled templates before replacing the snapshot.
Contact lists and form choices may change. Personal contact choices are omitted
from saved schemas; provide a verified current contact value when filling a form.

Run the offline checks:

```bash
python3 scripts/self_test.py
```

The 15 tests use a local HTTP stub to check signatures, drafts for all seven
forms, binary attachments, receipts, failed verification recovery, and malformed
responses. They do not contact Fermilab or create live logbook entries.

[SKILL.md](SKILL.md) is the Codex entrypoint. The active client is
[scripts/ECLAPI.py](scripts/ECLAPI.py); the supplied Python 2 original is
preserved unchanged at [assets/legacy/ECLAPI.py](assets/legacy/ECLAPI.py).
