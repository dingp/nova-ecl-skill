"""Configuration shared by posting and read-only form inspection."""
import json
import os
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent


def credentials(path=None, web=False, username=None):
    if not web and username is None:
        username = os.environ.get("ECL_USER")
    env_key = "ECL_WEB_CREDENTIALS_FILE" if web else "ECL_CREDENTIALS_FILE"
    filename = "ecl_access_web.txt" if web else "ecl_access.txt"
    candidate = Path(path or os.environ.get(env_key) or filename).expanduser()
    raw = candidate.read_text().strip()
    if raw.startswith("{"):
        record = json.loads(raw)
        user, password = record["user"], record["password"]
    else:
        lines = raw.splitlines()
        if len(lines) == 2:
            user, password = lines[0].strip(), lines[1].strip()
        elif len(lines) == 1 and not web:
            if not username:
                raise ValueError("Password-only credentials require --user or ECL_USER")
            user, password = username, lines[0].strip()
        else:
            raise ValueError("Credential file needs username on line 1 and password on line 2")
    if username is not None and username != user:
        raise ValueError("Requested username differs from the credential file username")
    if not user or not password:
        raise ValueError("Empty username or password")
    return user, password


def ecl_url(url=None, url_file=None):
    if url:
        return url.rstrip("/")
    if url_file:
        return Path(url_file).expanduser().read_text().strip().rstrip("/")
    if os.environ.get("ECL_URL"):
        return os.environ["ECL_URL"].rstrip("/")
    candidate = Path("ecl_url.txt")
    if not candidate.exists():
        candidate = SKILL_ROOT / "assets/ecl_url.txt"
    return candidate.read_text().strip().rstrip("/")
