"""Read the exact exported instrument version and its verified source bytes."""

import base64
import hashlib
import json
import os
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


def read_json(path):
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get("children", []))


def from_json(directory, celex, version_id):
    for name in (f"{celex}_structured.json", f"{celex}.json"):
        path = directory / name
        if not path.is_file():
            continue
        data = read_json(path)
        if data.get("celex") != celex or data["version"]["id"] != version_id:
            raise ValueError(f"{name} does not contain the requested CELEX and version")
        if "legal_elements" not in data:
            nodes = list(walk(
                node for roots in data["structure"].values() for node in roots
            ))
            data["legal_elements"] = [
                {k: v for k, v in node.items() if k not in {
                    "children", "references", "data_tables", "source_headings"
                }}
                for node in nodes
            ]
            data["element_references"] = [
                ref for node in nodes for ref in node["references"]
            ]
        return data
    raise ValueError(f"No {celex}_structured.json or {celex}.json in {directory}")


def postgres_dsn(env_file=None):
    if env_file is None:
        dsn = os.environ.get("LEGAL_RADAR_DATABASE_URL", "")
    else:
        from dotenv import dotenv_values

        if not env_file.is_file():
            raise ValueError(f"Database environment file not found: {env_file}")
        values = dotenv_values(env_file, encoding="utf-8-sig")
        dsn = values.get("LEGAL_RADAR_DATABASE_URL") or values.get("DATABASE_URL")
        if not dsn or not dsn.strip():
            raise ValueError(f"Set DATABASE_URL or LEGAL_RADAR_DATABASE_URL in {env_file}")
        dsn = dsn.strip()

    # Accept the SQLAlchemy driver suffix used in some backend configurations.
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix):]
    return dsn


def from_postgres(celex, version_id, dsn):
    try:
        import psycopg
    except ImportError as exc:
        raise ValueError("Install requirements-postgres.txt for PostgreSQL mode") from exc

    with psycopg.connect(dsn, connect_timeout=15) as connection:
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT to_jsonb(v), b.id, b.title_en
                FROM instrument_versions AS v
                JOIN legal_instruments AS i ON i.id = v.instrument_id
                JOIN source_documents AS b ON b.id = i.source_document_id
                WHERE b.external_id = %s AND v.id = %s::uuid
            """, (celex, version_id))
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"No version {version_id} for {celex}")
            version, base_id, title = row
            cursor.execute("""
                SELECT to_jsonb(e) FROM legal_elements AS e
                WHERE e.version_id = %s::uuid ORDER BY e.ordinal
            """, (version_id,))
            elements = [row[0] for row in cursor.fetchall()]
            cursor.execute("""
                SELECT to_jsonb(r)
                FROM element_references AS r
                JOIN legal_elements AS e ON e.id = r.source_element_id
                WHERE e.version_id = %s::uuid ORDER BY r.id
            """, (version_id,))
            refs = [row[0] for row in cursor.fetchall()]
            cursor.execute("""
                SELECT s.payload, to_jsonb(s) - 'payload'
                FROM source_snapshots AS s
                WHERE s.source_document_id = %s::uuid
                  AND s.snapshot_kind LIKE 'CONTENT%%'
                  AND s.payload IS NOT NULL
            """, (version.get("source_document_id") or base_id,))
            snapshots = [{"payload_bytes_raw": bytes(raw), "metadata": meta}
                         for raw, meta in cursor.fetchall()]
    return {
        "celex": celex, "title": title, "version": version,
        "legal_elements": elements, "element_references": refs,
        "raw_content_snapshots": snapshots,
    }


OFFICIAL_HOSTS = {"eur-lex.europa.eu", "publications.europa.eu", "data.europa.eu"}
MAX_BYTES = 40 * 1024 * 1024


def check_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS
            or parsed.port not in (None, 443) or parsed.username is not None):
        raise ValueError("The XHTML URL or redirect is outside the allowed official HTTPS hosts")


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def source_bytes(data, xhtml_dir, fetch):
    version = data["version"]
    digest = version.get("content_sha256")
    if not digest:
        raise ValueError("The selected version has no content SHA-256")
    snapshots = data.get("source_snapshots", []) + data.get("raw_content_snapshots", [])
    for item in snapshots:
        if "xhtml" in item:
            raw = item["xhtml"].encode("utf-8")
        elif "payload_bytes_raw" in item:
            raw = item["payload_bytes_raw"]
        else:
            raw = base64.b64decode(item["payload_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() == digest:
            return raw, "STORED_SNAPSHOT"
    if xhtml_dir is not None:
        path = xhtml_dir / f"{data['celex']}.xhtml"
        if path.is_file():
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError(f"{path.name} differs from the selected version's SHA-256")
            return raw, "LOCAL_XHTML"
    if fetch:
        url = version.get("source_url") or ""
        check_url(url)
        with build_opener(OfficialRedirects()).open(url, timeout=25) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("XHTML download exceeded the size limit")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Online XHTML differs from the selected version's SHA-256")
        return raw, "OFFICIAL_XHTML_URL"
    raise ValueError("No XHTML matching this version. Provide its saved .xhtml or enable --fetch-xhtml")
