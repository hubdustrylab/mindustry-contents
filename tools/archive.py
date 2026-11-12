"""Validate records and build the archive catalog."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

GUILD = "391020510269669376"
CHANNELS = {"maps": "416719902641225732", "schematics": "640604827344306207"}
DISCORD_EPOCH = datetime(2015, 1, 1, tzinfo=timezone.utc)
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FORMATS = {"msav", "msch", "png", "jpg", "webp", "gif"}
RFC3339 = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)\Z")


def require(test, message):
    if not test:
        raise ValueError(message)


def snowflake(value):
    require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value), "Invalid snowflake")
    number = int(value)
    require(number < 1 << 64, "Snowflake exceeds 64 bits")
    return number


def instant(value):
    require(isinstance(value, str) and RFC3339.fullmatch(value), "Expected an RFC 3339 timestamp")
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def discord_time(message_id):
    return DISCORD_EPOCH + timedelta(milliseconds=snowflake(message_id) >> 22)


def commit_epoch(value):
    return (instant(value) - UNIX_EPOCH) // timedelta(seconds=1)


def post_time(message_id, timestamp):
    from_id = discord_time(message_id)
    if timestamp is None:
        return utc(from_id), "message_id"
    from_post = instant(timestamp)
    if max(from_id.year, from_post.year) >= 2026:
        require(from_post.replace(microsecond=from_post.microsecond // 1000 * 1000) == from_id,
                "Discord timestamp and message ID disagree")
    return utc(from_post), "discord"


def record_path(collection, message_id, published_at):
    require(collection in CHANNELS, "Unknown collection")
    time = instant(published_at)
    prefix = f"{time:%Y%m%dT%H%M%S.%fZ}_{snowflake(message_id):020}"
    return PurePosixPath(collection, f"{time:%Y}", f"{time:%m}", f"{time:%d}", prefix + ".json")


def file_path(record, ordinal, name, fmt):
    require(isinstance(ordinal, int) and 1 <= ordinal <= 999, "Invalid file ordinal")
    require(fmt in FORMATS, "Unknown file format")
    stem = PurePosixPath(name.replace("\\", "/")).stem
    ascii_name = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:56].rstrip("-") or "content"
    return record.with_name(f"{record.stem}_{ordinal:03}_{slug}.{fmt}")


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_blob(root, path, size, digest):
    require(type(size) is int and size >= 0, "Invalid file size")
    require(isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest), "Invalid SHA-256")
    target = root / path
    require(target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(root), f"Missing or invalid file: {path}")
    require(target.stat().st_size == size, f"Size mismatch: {path}")
    with target.open("rb") as stream:
        require(hashlib.file_digest(stream, "sha256").hexdigest() == digest, f"Hash mismatch: {path}")


def check_record(root, path, record):
    source = record["source"]
    collection = record["collection"]
    require(record["schema_version"] == 1, "Unknown record version")
    require(source["guild_id"] == GUILD and source["root_channel_id"] == CHANNELS[collection], "Source outside the archive")
    snowflake(source["channel_id"])
    message_id = source["message_id"]
    published_at, basis = post_time(message_id, source["timestamp_raw"])
    require((published_at, basis) == (source["published_at"], source["timestamp_basis"]), "Invalid publication time")
    expected = record_path(collection, message_id, published_at)
    require(path == expected, f"Expected path: {expected}")
    require(source["url"] == f"https://discord.com/channels/{GUILD}/{source['channel_id']}/{message_id}", "Invalid message URL")
    for field in ("observed_at", "edited_at", "submitted_at"):
        if source[field] is not None:
            instant(source[field])
    instant(record["archived_at"])
    revision = record["revision"]
    revision_times = {
        "published_at": source["published_at"],
        "edited_at": source["edited_at"],
        "observed_at": source["observed_at"],
    }
    require(revision["basis"] in revision_times and revision["time"] == revision_times[revision["basis"]], "Invalid revision time")
    instant(revision["time"])
    seen = set()
    catalog_files = []
    primary_roles = {"map", "schematic"}
    require(any(file["role"] in primary_roles for file in record["files"]), "Record has no content file")
    for ordinal, file in enumerate(record["files"], 1):
        require(file["role"] in primary_roles | {"preview"}, "Unknown file role")
        if file["role"] in primary_roles:
            require((collection, file["role"]) in {("maps", "map"), ("schematics", "schematic")}, "File is in the wrong collection")
        origin = file["origin"]
        if origin.get("locator"):
            parsed = urlsplit(origin["locator"])
            require(parsed.scheme == "https" and parsed.netloc in {"cdn.discordapp.com", "media.discordapp.net"}
                    and not parsed.query and not parsed.fragment, "Invalid public media locator")
        if file["availability"] == "stored":
            file_name = file_path(expected, ordinal, file["original_name"], file["format"])
            require(file["path"] == file_name.as_posix(), f"Expected path: {file_name}")
            verify_blob(root, file_name, file["bytes"], file["sha256"])
            seen.add(file_name)
            detail = file.get("detail")
            if detail is not None:
                detail_path = file_name.with_name(file_name.name + ".metadata.json.gz")
                require(detail["path"] == detail_path.as_posix(), f"Expected path: {detail_path}")
                verify_blob(root, detail_path, detail["bytes"], detail["sha256"])
                seen.add(detail_path)
        else:
            require(file["availability"] in {"unavailable", "pending", "unsupported"}, "Invalid file status")
            require(all(file.get(key) is None for key in ("path", "bytes", "sha256", "detail")), "Missing file has a blob")
        catalog_files.append({key: file[key] for key in ("role", "format", "availability", "path", "bytes", "sha256")})
    entry = {
        "collection": collection, "message_id": message_id, "published_at": published_at,
        "record": path.as_posix(), "files": catalog_files,
    }
    return entry, seen


def catalog(root):
    entries = []
    expected_files = set()
    for collection in CHANNELS:
        folder = root / collection
        for path in folder.glob("*/*/*/*.json"):
            relative = PurePosixPath(path.relative_to(root).as_posix())
            try:
                entry, files = check_record(root, relative, load_json(path))
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(f"{relative}: {error}") from error
            entries.append(entry)
            expected_files.update(files | {relative})
        actual_files = {PurePosixPath(path.relative_to(root).as_posix()) for path in folder.rglob("*") if path.is_file()}
        require(actual_files <= expected_files, f"Unreferenced files in {collection}: {sorted(actual_files - expected_files)[:3]}")
    entries.sort(key=lambda entry: (entry["published_at"], int(entry["message_id"])))
    require(len({entry["message_id"] for entry in entries}) == len(entries), "Duplicate message records")
    return entries


def catalog_bytes(entries):
    return "".join(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n" for row in entries).encode("ascii")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8").strip()


def check_git(root, base=None):
    target = f"{base}..HEAD" if base else "HEAD"
    commits = git(root, "log", "--format=%H %at %ct", target, "--", *CHANNELS).splitlines()
    for row in commits:
        oid, author, committer = row.split()
        files = git(root, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", oid, "--", *CHANNELS).splitlines()
        records = [name for name in files if name.endswith(".json")]
        require(len(records) == 1, f"Data commit must change one record: {oid}")
        record = json.loads(git(root, "show", f"{oid}:{records[0]}"))
        epoch = commit_epoch(record["revision"]["time"])
        require(int(author) == epoch == int(committer), f"Git dates differ from the record: {oid}")
    return len(commits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "index"])
    parser.add_argument("--git", action="store_true", help="Check Git author and committer dates")
    parser.add_argument("--from", dest="base", help="Check commits added after this revision")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        entries = catalog(root)
        expected = catalog_bytes(entries)
        index = root / "catalog.jsonl"
        if args.command == "index":
            index.write_bytes(expected)
        else:
            require(index.read_bytes() == expected, "The catalog is out of date")
        if args.git:
            print(f"Verified {check_git(root, args.base)} data commits.")
        print(f"Verified {len(entries)} records.")
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
