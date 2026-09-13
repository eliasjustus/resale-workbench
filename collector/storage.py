"""Immutable local evidence packets and a small SQLite collection index."""

import html
import json
import sqlite3
from pathlib import Path


def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'collector.sqlite3')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS listings (
            listing_id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL,
            first_seen_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS captures (
            capture_id TEXT PRIMARY KEY, listing_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL, status TEXT NOT NULL,
            packet_path TEXT NOT NULL);
    ''')
    return db


def first_seen(db, listing_id, url, now):
    with db:
        db.execute('INSERT OR IGNORE INTO listings VALUES (?, ?, ?)', (listing_id, url, now))
    return db.execute('SELECT first_seen_at FROM listings WHERE listing_id=?', (listing_id,)).fetchone()[0]


def save_packet(db, folder, record):
    folder = Path(folder)
    (folder / 'record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    description = record['fields']['description']['value']
    if description is not None:
        (folder / 'description.txt').write_text(description, encoding='utf-8')
    escape = lambda v: html.escape(str(v), quote=True)
    rows = ''.join(f'<tr><th>{escape(k)}</th><td><pre>{escape(v["value"])}</pre></td>'
                   f'<td>{escape(v["status"])}</td></tr>' for k, v in record['fields'].items())
    photos = ''.join(f'<figure><a href="{escape(p["local_path"])}">'
                     f'<img src="{escape(p["local_path"])}" loading="lazy"></a>'
                     f'<figcaption>Photo {p["index"]} · {p["width"]} × {p["height"]}'
                     f'<br>{escape(p.get("download_note", ""))}</figcaption></figure>'
                     for p in record['photos'] if p['status'] == 'downloaded')
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'">
<title>Listing {escape(record['listing_id'])} evidence</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#17252a}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #ddd}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;margin:0}}figure{{display:inline-block;width:42%;vertical-align:top}}
img{{max-width:100%;max-height:600px}}.note{{background:#f2f3f4;padding:16px}}</style>
<h1>Listing {escape(record['listing_id'])}</h1>
<p><a href="{escape(record['canonical_url'])}" rel="noreferrer">Original listing</a> · {escape(record['collection_status'])}</p>
<p class="note">Collected evidence only. Seller claims are unverified. No valuation has been performed.
This review includes the asking price; it is not a blinded model input.</p>
<p>{escape(record['timestamps'])}</p><p>Issues: {escape(record['issues'])}</p>
<table>{rows}</table><h2>Gallery</h2><p>{escape(record['gallery'])}</p>{photos}</html>'''
    (folder / 'review.html').write_text(page, encoding='utf-8')
    with db:
        db.execute('INSERT INTO captures VALUES (?, ?, ?, ?, ?)',
                   (record['capture_id'], record['listing_id'], record['timestamps']['details_fetched_at']
                    or record['timestamps']['attempted_at'], record['collection_status'], str(folder.resolve())))
