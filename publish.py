#!/usr/bin/env python3
"""Turn audio files dropped in inbox/ into a podcast RSS feed (feed.xml).

Usage:
    python3 publish.py           # process inbox/, rebuild feed.xml
    python3 publish.py --push    # same, then git commit + push (GitHub Pages)
    python3 publish.py --watch   # watch ~/Downloads for new audio, auto-publish

Episode titles come from the file name. Edit episodes.json to change
titles/descriptions, then run again to rebuild the feed.
"""
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text())
INBOX = ROOT / "inbox"
EPISODES = ROOT / "episodes"
ORIGINALS = ROOT / "originals"  # gitignored backup of the raw downloads
DB = ROOT / "episodes.json"
SEEN = ROOT / "published.json"  # watch-dir file names already picked up (never re-added)
AUDIO_EXT = {".wav", ".m4a", ".mp3", ".mp4", ".ogg", ".aac", ".flac", ".webm"}


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "episode"


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout
    return int(float(out.strip()))


def process_inbox(episodes):
    files = sorted(p for p in INBOX.iterdir() if p.suffix.lower() in AUDIO_EXT)
    for src in files:
        title = re.sub(r"[_-]+", " ", src.stem).strip()
        now = datetime.now(timezone.utc)
        name = f"{now:%Y-%m-%d-%H%M%S}-{slugify(title)}.mp3"
        dst = EPISODES / name
        print(f"converting {src.name} -> episodes/{name}")
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vn",
             "-ac", "1", "-b:a", CONFIG.get("bitrate", "96k"), str(dst)],
            check=True)
        episodes.append({
            "title": title,
            "description": "",
            "file": name,
            "date": format_datetime(now),
            "duration": duration(dst),
            "bytes": dst.stat().st_size,
        })
        src.unlink()  # inbox holds copies; the original stays where it was
    return len(files)


def build_feed(episodes):
    base = CONFIG["base_url"].rstrip("/")
    c = {k: escape(str(v)) for k, v in CONFIG.items()}
    items = []
    for ep in reversed(episodes):  # newest first
        h, rem = divmod(ep["duration"], 3600)
        m, s = divmod(rem, 60)
        url = f"{base}/episodes/{ep['file']}"
        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <description>{escape(ep['description'] or ep['title'])}</description>
      <guid isPermaLink="false">{escape(ep['file'])}</guid>
      <pubDate>{ep['date']}</pubDate>
      <enclosure url="{escape(url)}" length="{ep['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{h:02d}:{m:02d}:{s:02d}</itunes:duration>
    </item>""")
    image = ""
    if (ROOT / "cover.jpg").exists():
        image = f'    <itunes:image href="{base}/cover.jpg"/>\n'
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{c['title']}</title>
    <link>{escape(base)}</link>
    <description>{c['description']}</description>
    <language>{c.get('language', 'en')}</language>
    <itunes:author>{c['author']}</itunes:author>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>Yes</itunes:block>
{image}{chr(10).join(items)}
  </channel>
</rss>
"""
    (ROOT / "feed.xml").write_text(feed)


def push():
    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        print("nothing to push")
        return
    subprocess.run(["git", "commit", "-qm", "Update podcast"], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "-q"], cwd=ROOT, check=True)
    print("pushed; GitHub Pages updates in ~1 min")


def watch():
    """Poll the watch dir; copy audio not yet published into inbox/ and publish.

    Files are copied, never moved, so the watch dir is left untouched.
    """
    wdir = Path(CONFIG.get("watch_dir", "~/Downloads")).expanduser()
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    sizes = {}
    print(f"watching {wdir} for audio (Ctrl+C to stop)")
    while True:
        ready = []
        for p in wdir.iterdir():
            if p.name in seen or p.suffix.lower() not in AUDIO_EXT or not p.is_file():
                continue
            size = p.stat().st_size
            if size > 0 and sizes.get(p) == size:  # size stable -> download finished
                ready.append(p)
            sizes[p] = size
        for p in ready:
            print(f"new file: {p.name}")
            shutil.copy2(p, INBOX / p.name)
            sizes.pop(p, None)
            seen.add(p.name)
        if ready:
            SEEN.write_text(json.dumps(sorted(seen), indent=2) + "\n")
        if ready:
            try:
                run(push_after=True)
            except subprocess.CalledProcessError as e:
                print(f"error: {e}")
        time.sleep(5)


def run(push_after):
    episodes = json.loads(DB.read_text()) if DB.exists() else []
    n = process_inbox(episodes)
    DB.write_text(json.dumps(episodes, indent=2) + "\n")
    build_feed(episodes)
    print(f"{n} new, {len(episodes)} total episodes")
    print(f"feed: {CONFIG['base_url'].rstrip('/')}/feed.xml")
    if push_after:
        push()


def main():
    for d in (INBOX, EPISODES, ORIGINALS):
        d.mkdir(exist_ok=True)
    if "--watch" in sys.argv:
        try:
            run(push_after=True)  # flush anything already in inbox/
        except subprocess.CalledProcessError as e:
            print(f"error: {e}")
        watch()
    else:
        run(push_after="--push" in sys.argv)


if __name__ == "__main__":
    main()
