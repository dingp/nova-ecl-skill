"""Shared draft/post command for the generated form scripts; no automatic POST retries."""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from ECLAPI import ECLAPIException, ECLConnection, ECLEntry, created_entry_id
from ecl_config import SKILL_ROOT, credentials, ecl_url


def schemas():
    return json.loads((SKILL_ROOT / "references/forms.json").read_text())["forms"]


def scalar_value(field, value):
    """Use the supplied ECLAPI's string field representation; never guess list encoding."""
    if field["type"] == "checkbox":
        if isinstance(value, bool):
            return "True" if value else "False"
        if value in ("true", "True", "false", "False"):
            return value.capitalize()
        raise ValueError("{} needs a boolean (true/false)".format(field["name"]))
    if isinstance(value, list):
        if len(value) != 1 or not field["multiple"]:
            raise ValueError("{}: XML list encoding is unverified; use one choice or the web UI".format(field["name"]))
        value = value[0]
    if not isinstance(value, str):
        raise ValueError("{} needs a string; use null to omit it".format(field["name"]))
    if field["choices"]:
        if value not in field["choices"]:
            raise ValueError("{}: invalid choice {!r}; run --list-fields for exact choices".format(field["name"], value))
        if value in ("[Choose one]", "(Choose one)"):
            raise ValueError("{} still has the dropdown placeholder".format(field["name"]))
    return value


def build_entry(form, schema, record, user, base_dir):
    if record.get("form", form) != form:
        raise ValueError("Input form differs from this script's form")
    allowed_keys = {"form", "category", "subject", "tags", "fields", "images", "attachments", "private", "related"}
    unknown_keys = set(record) - allowed_keys
    if unknown_keys:
        raise ValueError("Unknown input keys: " + ", ".join(sorted(unknown_keys)))
    subject = record.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("Provide --subject or a nonempty subject in the input JSON")
    category = record.get("category", schema.get("category", "General"))
    if not isinstance(category, str) or not category.strip():
        raise ValueError("Category must be a nonempty string")
    tags = record.get("tags", schema.get("tags", []))
    if not isinstance(tags, list) or any(not isinstance(t, str) or not t for t in tags):
        raise ValueError("Tags must be a list of nonempty strings")
    private = record.get("private", False)
    if not isinstance(private, bool):
        raise ValueError("private must be a boolean")
    entry = ECLEntry(category, tags=list(dict.fromkeys(tags)), formname=form,
                     private=private, related=record.get("related"))
    entry.addSubject(subject)
    if user:
        entry.setAuthor(user)
    definitions = {field["name"]: field for field in schema["fields"]}
    values = record.get("fields", {})
    if not isinstance(values, dict):
        raise ValueError("fields must be an object mapping exact field names to values")
    unknown = set(values) - set(definitions)
    if unknown:
        raise ValueError("Unknown fields: " + ", ".join(sorted(unknown)))
    count = 0
    for name, field in definitions.items():
        value = values.get(name)
        if value is None:
            if field.get("html_required"):
                raise ValueError("Missing HTML-required field: " + name)
            continue
        entry.setValue(name, scalar_value(field, value))
        count += 1
    if not count:
        raise ValueError("Provide at least one field; blank templates cannot be posted")
    for key, method in (("images", entry.addImage), ("attachments", entry.addAttachment)):
        for attachment in record.get(key, []):
            if set(attachment) != {"name", "path"} or not attachment["name"]:
                raise ValueError(key + " items need name and path")
            path = Path(attachment["path"]).expanduser()
            if not path.is_absolute():
                path = base_dir / path
            method(attachment["name"], str(path))
    return entry


def verify_entry(connection, eid, entry, schema):
    actual = ET.fromstring(connection.get(eid))
    expected = ET.fromstring(entry.xshow())
    mismatches, unverified = [], []
    for key in ("author", "category", "subject"):
        if actual.get(key) != expected.get(key):
            mismatches.append(key)
    actual_form, expected_form = actual.find("form"), expected.find("form")
    if actual_form is None or actual_form.get("name") != expected_form.get("name"):
        mismatches.append("form")
    values = {} if actual_form is None else {n.get("name"): n.text or "" for n in actual_form.findall("field")}
    definitions = {f["name"]: f for f in schema["fields"]}
    for field in expected_form.findall("field"):
        name, value = field.get("name"), field.text or ""
        if definitions[name]["multiple"] and values.get(name) == ",":
            unverified.append(name)
        elif values.get(name) != value:
            mismatches.append("field:" + name)
    actual_tags = {n.get("name") for n in actual.findall("tag")}
    if not {n.get("name") for n in expected.findall("tag")} <= actual_tags:
        mismatches.append("tags")
    actual_files = {n.get("filename") for n in actual.findall("attachment")}
    if not {n.get("filename") for n in expected.findall("attachment")} <= actual_files:
        mismatches.append("attachments")
    return {"verified": not mismatches and not unverified, "mismatches": mismatches,
            "unverified_multiselect_fields": unverified}


def save_receipt(path, receipt, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
    else:
        descriptor, temporary = tempfile.mkstemp(prefix=".ecl-receipt-", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(receipt, stream, indent=2)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def post_once(connection, entry, schema, receipt_path=None):
    fingerprint = hashlib.sha256(connection._url.encode() + b"\0" + entry.xshow()).hexdigest()
    path = Path(receipt_path) if receipt_path else Path.cwd() / ".ecl-receipts" / (fingerprint + ".json")
    receipt = {"fingerprint": fingerprint, "url": connection._url, "state": "pending",
               "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        save_receipt(path, receipt, exclusive=True)
    except FileExistsError:
        receipt = json.loads(path.read_text())
        if receipt.get("fingerprint") != fingerprint:
            raise ValueError("Receipt belongs to different content: " + str(path))
        if receipt.get("state") != "created" or not receipt.get("entry_id"):
            raise ValueError("Previous submission is {}; inspect {} before another POST".format(receipt.get("state"), path))
    else:
        try:
            code, message, response = connection.post(entry)
        except Exception:
            receipt["state"] = "uncertain"
            save_receipt(path, receipt)
            raise RuntimeError("POST outcome unknown; do not repeat it. Inspect receipt " + str(path)) from None
        receipt["http_status"] = code
        eid = created_entry_id(response) if 200 <= code < 300 else None
        if eid is None:
            receipt["state"] = "rejected" if 400 <= code < 500 else "uncertain"
            save_receipt(path, receipt)
            raise RuntimeError("HTTP {} {}; no entry ID confirmed. Inspect {} before retrying".format(code, message, path))
        receipt.update(state="created", entry_id=eid)
        save_receipt(path, receipt)
    eid = receipt["entry_id"]
    print("Created entry {}: {}/E/show?e={}".format(eid, connection._url, eid), flush=True)
    try:
        receipt["verification"] = verify_entry(connection, eid, entry, schema)
    except Exception:
        save_receipt(path, receipt)
        raise RuntimeError("Entry {} was created, but read-back failed. Reuse this receipt for GET-only verification: {}".format(eid, path)) from None
    save_receipt(path, receipt)
    print(json.dumps(receipt["verification"], indent=2))
    print("Receipt: " + str(path))
    return 4 if receipt["verification"]["mismatches"] else 0


def main(form="default"):
    schema = schemas().get(form, {"category": "General", "tags": [], "fields": [
        {"name": "text", "type": "textarea", "multiple": False, "choices": [], "html_required": False}
    ]})
    parser = argparse.ArgumentParser(description="Prepare {} XML; --post submits one entry.".format(form))
    parser.add_argument("--input", help="JSON template/record; relative attachment paths use its directory")
    parser.add_argument("--subject")
    parser.add_argument("--category")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--image", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--attach", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--text")
    parser.add_argument("--text-file")
    parser.add_argument("--credentials", help="API credentials, never the web credential file")
    parser.add_argument("--user")
    parser.add_argument("--url")
    parser.add_argument("--url-file")
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--receipt")
    parser.add_argument("--output", help="Write draft or --get XML to a file")
    parser.add_argument("--list-fields", action="store_true")
    parser.add_argument("--write-template", metavar="PATH")
    parser.add_argument("--get", type=int, metavar="ENTRY_ID", help="Read an existing entry; no POST")
    parser.add_argument("--timeout", type=float, default=30)
    for index, field in enumerate(schema["fields"]):
        flag = "--field-" + re.sub(r"[^a-z0-9]+", "-", field["name"].lower()).strip("-")
        parser.add_argument(flag, dest="field_" + str(index), help=field.get("label", field["name"]))
    args = parser.parse_args()
    try:
        if args.list_fields:
            print(json.dumps(schema, ensure_ascii=False, indent=2))
            return
        if args.write_template:
            template = {"form": form, "category": schema["category"], "subject": None,
                        "tags": schema["tags"], "fields": {f["name"]: None for f in schema["fields"]},
                        "images": [], "attachments": []}
            with Path(args.write_template).open("x") as stream:
                json.dump(template, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print("Template: " + args.write_template)
            return
        base = ecl_url(args.url, args.url_file)
        if urllib.parse.urlsplit(base).scheme != "https" and not args.allow_http:
            raise ValueError("HTTP requires an explicitly chosen URL and --allow-http")
        user = args.user or os.environ.get("ECL_USER")
        connection = None
        if args.post or args.get is not None:
            user, password = credentials(args.credentials, username=user)
            connection = ECLConnection(base, user, password, timeout=args.timeout)
        if args.get is not None:
            if args.post:
                raise ValueError("--get and --post are mutually exclusive")
            content = connection.get(args.get)
            if args.output:
                Path(args.output).write_bytes(content)
            else:
                print(content.decode())
            return
        record = json.loads(Path(args.input).read_text()) if args.input else {}
        if args.subject is not None:
            record["subject"] = args.subject
        if args.category is not None:
            record["category"] = args.category
        if args.tag:
            record["tags"] = list(record.get("tags", schema["tags"])) + args.tag
        values = record.setdefault("fields", {})
        for assignment in args.set:
            if "=" not in assignment:
                raise ValueError("--set expects FIELD=VALUE")
            name, value = assignment.split("=", 1)
            values[name] = value
        for index, field in enumerate(schema["fields"]):
            value = getattr(args, "field_" + str(index))
            if value is not None:
                values[field["name"]] = value
        if args.text is not None or args.text_file:
            if form != "default" or (args.text is not None and args.text_file):
                raise ValueError("Use one of --text/--text-file, only with post_entry.py")
            values["text"] = args.text if args.text is not None else Path(args.text_file).read_text()
        base_dir = Path(args.input).resolve().parent if args.input else Path.cwd()
        for key, assignments in (("images", args.image), ("attachments", args.attach)):
            for assignment in assignments:
                if "=" not in assignment:
                    raise ValueError("Attachment arguments expect NAME=PATH")
                name, path = assignment.split("=", 1)
                record.setdefault(key, []).append({"name": name, "path": str(Path(path).expanduser().resolve())})
        entry = build_entry(form, schema, record, user, base_dir)
        if args.output:
            Path(args.output).write_bytes(entry.xshow() + b"\n")
        if args.post:
            raise SystemExit(post_once(connection, entry, schema, args.receipt))
        if not args.output:
            print(entry.xshow().decode())
        print("Draft only; {} supplied, {} omitted fields.{}".format(
            len(entry._form), len(schema["fields"]) - len(entry._form),
            " Saved " + args.output if args.output else ""), file=sys.stderr)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, ECLAPIException, urllib.error.URLError) as exc:
        parser.exit(2, "Error: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
