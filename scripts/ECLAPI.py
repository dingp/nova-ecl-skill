"""Python 3 adaptation of the supplied Fermilab ECLAPI client (stdlib only).

The original Python 2 source is retained in assets/legacy/ECLAPI.py.
NOvA HTTPS accepts the signed XML API without an X-Password header.
"""

import base64
import hashlib
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as etree
from pathlib import Path


class ECLAPIException(Exception):
    pass


class ECLHTTPError(ECLAPIException):
    def __init__(self, code, msg, body=b""):
        self.Code, self.Message, self.Body = code, msg, body
        super().__init__("HTTP {}: {}".format(code, msg))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ECLConnection:
    SignatureMethod = "md5"

    def __init__(self, url, username=None, password=None, xml_user=True, timeout=30):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise ValueError("ECL URL must be an HTTP(S) logbook base URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use a base URL without credentials, query, or fragment")
        self._url = url.rstrip("/")
        self._username, self._password = username, password
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _make_salt(self):
        return secrets.token_hex(16)

    def _signature(self, method, args, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        message = args.encode("utf-8") + b":" + self._password.encode("utf-8") + b":" + body
        return hashlib.new(method, message).hexdigest()

    def _request(self, endpoint, pairs=(), body=None):
        query = urllib.parse.urlencode(list(pairs) + [("salt", self._make_salt())])
        headers = {}
        if self._username:
            if not self._password:
                raise ValueError("A password is required for a signed request")
            headers = {
                "X-User": self._username,
                "X-Signature-Method": self.SignatureMethod,
                "X-Signature": self._signature(self.SignatureMethod, query, body or b""),
            }
        if body is not None:
            headers["Content-Type"] = "text/xml"
        request = urllib.request.Request(
            self._url + "/E/" + endpoint + "?" + query, data=body, headers=headers
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.status, response.reason, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.reason, exc.read()

    def post(self, entry):
        if self._username:
            entry.setAuthor(self._username)
        return self.post_xml(entry.xshow().strip())

    def post_xml(self, xml):
        if isinstance(xml, str):
            xml = xml.encode("utf-8")
        return self._request("xml_post", body=xml)

    def list(self, category=None, form=None, tag=None, limit=None, after=None,
             substring=None, text=None):
        pairs = [("o", "ids")]
        if after is not None and not isinstance(after, str):
            after = after.strftime("%Y-%m-%d %H:%M:%S")
        for key, value in (("c", category), ("f", form), ("t", tag), ("l", limit),
                           ("a", after), ("st", substring), ("si", text)):
            if value is not None:
                pairs.append((key, str(value)))
        code, msg, body = self._request("xml_search", pairs)
        if code != 200:
            raise ECLHTTPError(code, msg, body)
        return [int(node.attrib["id"]) for node in etree.fromstring(body).findall("entry")]

    def get(self, eid):
        code, msg, body = self._request("xml_get", [("e", str(int(eid)))])
        if code != 200:
            raise ECLHTTPError(code, msg, body)
        return body

    def close(self):
        pass


class ECLEntry:
    def __init__(self, category, tags=None, formname="default", text="", preformatted=False,
                 private=False, textile=False, related=None):
        self._entry = etree.Element("entry", category=category)
        if not preformatted:
            self._entry.set("formatted", "yes")
        if textile:
            self._entry.set("html_safe", "no")
        if private:
            self._entry.set("private", "yes")
        if related is not None:
            self._entry.set("related", str(related))
        self._form = etree.SubElement(self._entry, "form", name=formname)
        if text:
            self.setValue("text", text)
        for tag in tags or []:
            etree.SubElement(self._entry, "tag", name=tag)

    def setValue(self, name, value):
        if not isinstance(value, str):
            raise TypeError("Form field values must be strings")
        field = next((node for node in self._form if node.get("name") == name), None)
        if field is None:
            field = etree.SubElement(self._form, "field", name=name)
        field.text = value

    def setAuthor(self, name):
        self._entry.set("author", name)

    def addSubject(self, text):
        self._entry.set("subject", text)

    def _attachment(self, kind, name, filename, data):
        raw = Path(filename).read_bytes() if data is None else data
        if not isinstance(raw, bytes):
            raise TypeError("Attachment data must be bytes")
        node = etree.SubElement(self._entry, "attachment", type=kind, name=name,
                                filename=Path(filename).name)
        node.text = base64.b64encode(raw).decode("ascii")

    def addAttachment(self, name, filename, data=None):
        self._attachment("file", name, filename, data)

    def addImage(self, name, filename, image=None):
        self._attachment("image", name, filename, image)

    def xshow(self):
        return etree.tostring(self._entry, encoding="utf-8")


def created_entry_id(response):
    """NOvA returns plain text 'Created 257947', rather than an XML response."""
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    match = re.fullmatch(r"\s*Created\s+(\d+)\s*", response)
    if match:
        return int(match.group(1))
    try:
        root = etree.fromstring(response)
    except etree.ParseError:
        return None
    candidates = ([root] if root.tag in ("entry", "created") else []) + list(root.iter("entry"))
    for node in candidates:
        if node.get("id", "").isdigit():
            return int(node.get("id"))
    return None
