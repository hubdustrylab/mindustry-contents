# Mindustry Contents

Maps and schematics from the [Mindustry Discord](https://discord.com/channels/391020510269669376). Start with the [catalog](catalog.jsonl) or browse [maps](maps/) and [schematics](schematics/). See [coverage.json](coverage.json) for the current import status.

## File names

Files are grouped by UTC date. A record and its files share the same prefix.

    maps/2026/11/11/20261111T170837.469000Z_01570123095606296716.json
    maps/2026/11/11/20261111T170837.469000Z_01570123095606296716_001_sector-222-attack.msav

The prefix contains the UTC timestamp and the message ID padded to 20 digits. A file name then has a three digit ordinal, a short ASCII name and an extension. The record keeps the original name, path, size and SHA-256. Use the record for file lookup.

## Metadata

[schema/record.v1.json](schema/record.v1.json) describes the records. Each line in catalog.jsonl contains a record path, its Discord message ID, its publication time and its files. Read large file metadata from the gzip JSON path in the record. Metadata includes its extraction status. The original bytes remain available when an extraction is incomplete.

Discord transport identity, submitter claims and file author claims have separate fields. A missing identity or time is null. Source text keeps its original language.

Data commits use the Discord post time for both Git dates. Git stores whole seconds. The record preserves the source timestamp and its fractional part. An edit uses its observed source edit time. Maintenance commits use their actual time.

Run the check command to verify the catalog and file hashes.

    python tools/archive.py check

Add --git in a full checkout to verify the dates of the data commits.
