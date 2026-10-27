"""Read live ECL form definitions; never submit an entry or store a session cookie."""
import argparse
import http.cookiejar
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from ecl_config import SKILL_ROOT, credentials, ecl_url

FORMS = ["2026_Control-Room_Checklist", "2026_Stable_Running_Checklist", "2025_Expert-contact",
         "2026_Start_Shift", "2026_Shift-check-in", "2026_Shift-check-out", "2026_Shift-summary"]


class FormParser(HTMLParser):
    """Streaming parser tolerating ECL's misspelled </optio> and table tags."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields, self.rows = [], []
        self.in_form = False
        self.row = None
        self.label = self.help = False
        self.control = None
        self.category_control = False
        self.categories = []

    def finish_row(self):
        if self.row:
            for field in self.row["fields"]:
                field["label"] = " ".join(self.row["label"]).strip().rstrip(":")
                field["help"] = " ".join(" ".join(self.row["help"]).split())
                field["links"] = self.row["links"]
                self.fields.append(field)
            self.rows.append(self.row)
        self.row = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = attrs.get("id") == "the_form"
        if not self.in_form:
            return
        if tag == "tr":
            self.finish_row()
            self.row = {"label": [], "help": [], "links": [], "fields": []}
            self.label = self.help = False
        if tag == "label":
            self.label = True
        if tag == "td" and "888888" in attrs.get("style", ""):
            self.help = True
        if tag == "a" and self.help and self.row and attrs.get("href"):
            self.row["links"].append(attrs["href"])
        if tag in ("input", "textarea", "select"):
            self.control = None
            self.category_control = attrs.get("name") == "_category_id"
            name = attrs.get("name", "")
            if self.row and self.row["label"] and name and not name.startswith("_"):
                kind = attrs.get("type", "text") if tag == "input" else tag
                if kind in ("submit", "button", "hidden", "file"):
                    return
                if kind == "radio":
                    existing = next((f for f in self.row["fields"] if f["name"] == name), None)
                    if existing:
                        existing["choices"].append(attrs["value"])
                        if "checked" in attrs:
                            existing["html_default"] = attrs["value"]
                        return
                self.control = {"name": name, "type": kind,
                                "html_required": "required" in attrs,
                                "multiple": "multiple" in attrs,
                                "choices": [], "html_default": attrs.get("value", "")}
                if kind == "checkbox":
                    self.control["html_default"] = "checked" in attrs
                elif kind == "radio":
                    self.control["choices"] = [attrs["value"]]
                    self.control["html_default"] = attrs["value"] if "checked" in attrs else None
                elif kind == "select":
                    self.control["html_default"] = [] if "multiple" in attrs else None
                self.row["fields"].append(self.control)
        if tag == "option":
            value = attrs.get("value", "")
            if self.category_control and value:
                self.categories.append(value)
            if self.control and self.control["type"] == "select":
                self.control["choices"].append(value)
                if self.control["multiple"]:
                    if "selected" in attrs:
                        self.control["html_default"].append(value)
                elif "selected" in attrs or self.control["html_default"] is None:
                    self.control["html_default"] = value
        if tag == "textarea" and self.control:
            self.control["html_default"] = ""

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.finish_row()
            self.in_form = False
        elif tag == "label":
            self.label = False
        elif tag == "td":
            self.help = False
        elif tag in ("select", "textarea"):
            self.control = None
            self.category_control = False

    def handle_data(self, data):
        if not self.in_form or not self.row:
            return
        if self.label:
            self.row["label"].append(data.strip())
        if self.help:
            self.row["help"].append(data)
        if self.control and self.control["type"] == "textarea":
            self.control["html_default"] += data


def parse_form(page):
    parser = FormParser()
    parser.feed(page)
    parser.finish_row()
    if not parser.fields:
        raise ValueError("No form fields found; authentication or page structure changed")
    for field in parser.fields:
        if field["name"] == "Current RunCo":
            # Contact choices change and can identify people. Keep them out of saved schemas.
            field["choices"] = []
            field["html_default"] = []
            field["dynamic_choices"] = True
    return parser.fields


def web_session(base, username, password, timeout=30):
    if urllib.parse.urlsplit(base).scheme != "https":
        raise ValueError("Web credentials must only be sent over HTTPS")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    # ECL prepends its host to ret_url: an absolute URL creates a malformed redirect.
    relative = urllib.parse.urlsplit(base).path + "/E/create_entry"
    data = urllib.parse.urlencode({"username": username, "password": password,
                                  "ret_url": relative}).encode()
    with opener.open(base + "/U/doLogin", data=data, timeout=timeout) as response:
        response.read()
        if "/U/login" in urllib.parse.urlsplit(response.url).path or not list(jar):
            raise ValueError("ECL web login failed; use the separate web credential file")
    return opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-credentials")
    parser.add_argument("--url")
    parser.add_argument("--url-file")
    parser.add_argument("--form", action="append", choices=FORMS)
    parser.add_argument("--output", help="Write the schema snapshot here (otherwise print JSON)")
    args = parser.parse_args()
    base = ecl_url(args.url, args.url_file)
    user, password = credentials(args.web_credentials, web=True)
    opener = web_session(base, user, password)
    old = json.loads((SKILL_ROOT / "references/forms.json").read_text())
    snapshot = {"source_url": base, "inspected_at": datetime.now(timezone.utc).isoformat(), "forms": {}}
    for name in args.form or FORMS:
        with opener.open(base + "/E/create_entry?" + urllib.parse.urlencode({"f": name}), timeout=30) as r:
            page = r.read().decode("utf-8")
        schema = dict(old["forms"].get(name, {}))
        schema["fields"] = parse_form(page)
        schema["source"] = base + "/E/create_entry?" + urllib.parse.urlencode({"f": name})
        snapshot["forms"][name] = schema
    output = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(output)
        print("Inspected {} forms; saved {}".format(len(snapshot["forms"]), args.output))
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
