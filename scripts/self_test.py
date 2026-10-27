"""Offline behavioral checks against a local ECL stub; never contact Fermilab."""
import contextlib
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ECLAPI import ECLConnection, ECLEntry, created_entry_id
from ecl_config import SKILL_ROOT, credentials
from form_cli import build_entry, post_once, schemas, verify_entry
from inspect_forms import parse_form

USERNAME = secrets.token_hex(12)
PASSWORD = secrets.token_hex(24)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    def authenticated(self, body):
        query = urllib.parse.urlsplit(self.path).query
        expected = hashlib.md5(query.encode() + b":" + PASSWORD.encode() + b":" + body).hexdigest()
        return (self.headers.get("X-User") == USERNAME and
                self.headers.get("X-Signature-Method") == "md5" and
                self.headers.get("X-Signature") == expected and
                self.headers.get("X-Password") is None)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if not self.authenticated(body):
            self.reply(403, b"bad signature")
            return
        self.server.state["posts"] += 1
        self.server.state["xml"] = body
        mode = self.server.state["mode"]
        if mode == "server_error":
            self.reply(500, b"unknown outcome")
        elif mode == "malformed":
            self.reply(200, b"<html>not a creation receipt</html>")
        elif mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/other")
            self.end_headers()
        else:
            self.reply(200, b"Created 42\n")

    def do_GET(self):
        if not self.authenticated(b""):
            self.reply(403, b"bad signature")
            return
        self.server.state["gets"] += 1
        if self.server.state["mode"] == "bad_get":
            self.reply(503, b"temporarily unavailable")
        else:
            self.reply(200, self.server.state["xml"])


class SkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.environment = patch.dict(os.environ, {"ECL_USER": USERNAME})
        self.environment.start()
        self.server.state = {"mode": "normal", "posts": 0, "gets": 0, "xml": b""}
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.client = ECLConnection("http://127.0.0.1:" + str(self.server.server_port), USERNAME, PASSWORD)
        self.schema = {"fields": [{"name": "text", "type": "textarea", "multiple": False, "choices": []}]}
        self.entry = ECLEntry("General", tags=["General"], text="UTF-8 μ & <escaped>\ntext")
        self.entry.setAuthor(USERNAME)
        self.entry.addSubject("Offline test")
        self.receipt = self.folder / "receipt.json"

    def tearDown(self):
        self.temp.cleanup()
        self.environment.stop()

    def submit(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = post_once(self.client, self.entry, self.schema, self.receipt)
        self.assertNotIn(PASSWORD, output.getvalue())
        return result

    def test_signed_post_readback_and_duplicate_receipt(self):
        self.assertEqual(self.submit(), 0)
        self.assertEqual(self.submit(), 0)
        self.assertEqual(self.server.state["posts"], 1)
        receipt = json.loads(self.receipt.read_text())
        self.assertTrue(receipt["verification"]["verified"])
        self.assertEqual(receipt["entry_id"], 42)

    def test_binary_attachments_survive(self):
        data = bytes(range(256))
        self.entry.addImage("screenshot", "picture.png", image=data)
        self.entry.addAttachment("empty", "empty.txt", data=b"")
        self.assertEqual(self.submit(), 0)
        doc = ET.fromstring(self.server.state["xml"])
        import base64
        self.assertEqual(base64.b64decode(doc.find("attachment").text), data)

    def test_unknown_success_cannot_be_reposted(self):
        self.server.state["mode"] = "malformed"
        with self.assertRaises(RuntimeError):
            self.submit()
        with self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.server.state["posts"], 1)
        self.assertEqual(json.loads(self.receipt.read_text())["state"], "uncertain")

    def test_server_error_cannot_be_reposted(self):
        self.server.state["mode"] = "server_error"
        with self.assertRaises(RuntimeError):
            self.submit()
        with self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.server.state["posts"], 1)

    def test_redirect_does_not_follow_or_repost(self):
        self.server.state["mode"] = "redirect"
        with self.assertRaises(RuntimeError):
            self.submit()
        self.assertEqual(self.server.state["gets"], 0)
        self.assertEqual(self.server.state["posts"], 1)

    def test_readback_failure_preserves_created_id(self):
        self.server.state["mode"] = "bad_get"
        with self.assertRaises(RuntimeError):
            self.submit()
        self.assertEqual(json.loads(self.receipt.read_text())["entry_id"], 42)
        self.server.state["mode"] = "normal"
        self.assertEqual(self.submit(), 0)
        self.assertEqual(self.server.state["posts"], 1)

    def test_multiselect_export_is_unverified(self):
        self.schema["fields"][0]["multiple"] = True
        actual = ET.fromstring(self.entry.xshow())
        actual.find("form/field").text = ","
        self.server.state["xml"] = ET.tostring(actual)
        result = verify_entry(self.client, 42, self.entry, self.schema)
        self.assertFalse(result["verified"])
        self.assertEqual(result["unverified_multiselect_fields"], ["text"])

    def test_every_form_drafts_with_exact_keys_and_cli(self):
        for name, schema in schemas().items():
            template = json.loads((SKILL_ROOT / "assets/templates" / (name + ".json")).read_text())
            self.assertEqual(set(template["fields"]), {f["name"] for f in schema["fields"]})
            self.assertTrue(all(value is None for value in template["fields"].values()))
            template["subject"] = "Offline form validation"
            for field in schema["fields"]:
                choices = [v for v in field["choices"] if v not in ("[Choose one]", "(Choose one)")]
                template["fields"][field["name"]] = False if field["type"] == "checkbox" else choices[0] if choices else "offline"
            input_path = self.folder / (name + ".json")
            input_path.write_text(json.dumps(template))
            output = self.folder / (name + ".xml")
            result = subprocess.run([sys.executable, str(SKILL_ROOT / "scripts" / schema["script"]),
                                     "--input", str(input_path), "--output", str(output)],
                                    cwd=self.folder, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ET.parse(output).find("form").get("name"), name)
        self.assertFalse((self.folder / ".ecl-receipts").exists())

    def test_invalid_choice_typo_and_ambiguous_list_rejected(self):
        name = "2026_Control-Room_Checklist"
        schema = schemas()[name]
        for fields in ({"cr06_windows_present": "Yes"}, {"cr01_time": "Yesterday"},
                       {"cr01_time": ["Within 5 minutes", "Older than 5 mins: calling DAQ expert"]}):
            with self.assertRaises(ValueError):
                build_entry(name, schema, {"subject": "Offline", "fields": fields}, USERNAME, self.folder)

    def test_get_cli_writes_requested_output_without_post(self):
        self.server.state["xml"] = self.entry.xshow()
        credential_file = self.folder / "test-access.txt"
        credential_file.write_text(USERNAME + "\n" + PASSWORD + "\n")
        output = self.folder / "entry.xml"
        result = subprocess.run([sys.executable, str(SKILL_ROOT / "scripts/post_entry.py"),
            "--get", "42", "--output", str(output), "--url", self.client._url,
            "--allow-http", "--credentials", str(credential_file)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), self.entry.xshow())
        self.assertEqual(self.server.state["posts"], 0)

    def test_error_xml_is_not_a_creation_receipt(self):
        self.assertIsNone(created_entry_id(b'<error id="42">Rejected</error>'))

    def test_username_is_configuration_only(self):
        path = self.folder / "credentials.txt"
        path.write_text(USERNAME + "\n" + PASSWORD + "\n")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(credentials(path), (USERNAME, PASSWORD))
            path.write_text(PASSWORD + "\n")
            with self.assertRaisesRegex(ValueError, "require --user or ECL_USER"):
                credentials(path)
            self.assertEqual(credentials(path, username=USERNAME), (USERNAME, PASSWORD))
            with patch.dict(os.environ, {"ECL_USER": USERNAME}):
                self.assertEqual(credentials(path), (USERNAME, PASSWORD))

    def test_unconfigured_draft_does_not_invent_an_author(self):
        schema = {"fields": [{"name": "text", "type": "textarea", "multiple": False, "choices": []}]}
        entry = build_entry("default", schema, {"subject": "Draft", "fields": {"text": "Draft"}}, None, self.folder)
        self.assertNotIn("author", ET.fromstring(entry.xshow()).attrib)

    def test_contact_choices_are_not_saved(self):
        page = '<form id="the_form"><tr><th><label>Contact:</label></th><td>'
        page += '<select name="Current RunCo" multiple><option value="' + USERNAME + '">Contact</option>'
        page += '</select></td></tr></form>'
        field = parse_form(page)[0]
        self.assertEqual(field["choices"], [])
        self.assertTrue(field["dynamic_choices"])
        self.assertNotIn(USERNAME, json.dumps(field))

    def test_parser_handles_actual_ecl_markup_errors(self):
        page = '''<form id="the_form"><tr><th><label>State:</label></td>
        <td><select name="cr06_windows _present" multiple>
        <option value="A &amp; B">A &amp; B</optio><option value="No">No</optio></select></td>
        <td style="color:#888888">Help</td></tr>
        <tr><th><label>Next:</label></td><td><input type="radio" name="Next Shifter" value="Yes">
        <input type="radio" name="Next Shifter" value="No"></td></tr></form>'''
        fields = parse_form(page)
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[0]["name"], "cr06_windows _present")
        self.assertEqual(fields[0]["choices"], ["A & B", "No"])
        self.assertEqual(fields[1]["choices"], ["Yes", "No"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
