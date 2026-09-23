#!/usr/bin/python3
"""Search local git mirrors of lore.kernel.org mailing lists.

$LORE_DIR (default ~/lore) holds bare `git clone --mirror`s of lore's
public-inbox v2 epochs (<list>/git/<N>.git: one commit per message, the raw
message in blob "m"), the list config (`lists`) and index.sqlite3: one row per
Message-ID, deduplicated across lists, plus a contentless FTS5 index. Message
text is always read back from git. `lore search --help` documents the query
syntax, which follows lore's own prefixes.
"""

import argparse
import concurrent.futures
import email
import email.header
import email.policy
import email.utils
import fcntl
import gzip
import itertools
import json
import multiprocessing
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone

LORE = "https://lore.kernel.org"
ROOT = os.path.expanduser(os.environ.get("LORE_DIR", "~/lore"))
DB = os.path.join(ROOT, "index.sqlite3")
LISTS = os.path.join(ROOT, "lists")
UA = "lore-mirror/1 (+git mirror of public-inbox epochs)"
GIT_ENV = dict(
    os.environ,
    GIT_TERMINAL_PROMPT="0",
    GIT_HTTP_LOW_SPEED_LIMIT="1000",
    GIT_HTTP_LOW_SPEED_TIME="120",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS msg (
  id INTEGER PRIMARY KEY,
  mid TEXT NOT NULL UNIQUE,
  date INTEGER NOT NULL,  -- Date: header, capped at received time + 1 day
  rt INTEGER NOT NULL,    -- received: the git commit time
  author TEXT NOT NULL,
  subject TEXT NOT NULL,
  irt TEXT,               -- In-Reply-To, else the last References entry
  tid TEXT NOT NULL,      -- first References entry, else irt, else mid
  kind TEXT NOT NULL,     -- p patch, c cover letter, r reply, g pull request, o other
  list TEXT NOT NULL, epoch INTEGER NOT NULL, blob TEXT NOT NULL  -- first copy seen
);
CREATE INDEX IF NOT EXISTS msg_tid ON msg(tid);
CREATE INDEX IF NOT EXISTS msg_irt ON msg(irt);
CREATE INDEX IF NOT EXISTS msg_date ON msg(date);
CREATE TABLE IF NOT EXISTS ml (list TEXT NOT NULL, id INTEGER NOT NULL, PRIMARY KEY (list, id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ml_id ON ml(id);
CREATE TABLE IF NOT EXISTS epoch (list TEXT NOT NULL, n INTEGER NOT NULL, tip TEXT, fp TEXT, PRIMARY KEY (list, n));
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  s, f, t, c, b, dfn, dfa, dfb,
  content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2'
);
"""

Row = namedtuple("Row", "id mid date author subject kind tid irt list epoch blob")
COLS = "msg.id, msg.mid, msg.date, msg.author, msg.subject, msg.kind, msg.tid, msg.irt, msg.list, msg.epoch, msg.blob"


def die(msg):
    print(f"lore: {msg}", file=sys.stderr)
    sys.exit(2)


def connect(write=False):
    if write:
        os.makedirs(ROOT, exist_ok=True)
        db = sqlite3.connect(DB, timeout=300)
        db.executescript(SCHEMA)
        return db
    if not os.path.exists(DB):
        die(f"no index at {DB}; run `lore sync`")
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    return db


def configured():
    try:
        with open(LISTS) as f:
            return [n for n in (line.split("#", 1)[0].strip() for line in f) if n]
    except FileNotFoundError:
        return []


def gitdir(lst, n):
    return os.path.join(ROOT, lst, "git", f"{n}.git")


def local_epochs(lst):
    try:
        names = os.listdir(os.path.join(ROOT, lst, "git"))
    except FileNotFoundError:
        return []
    return sorted(int(m[1]) for m in (re.fullmatch(r"(\d+)\.git", x) for x in names) if m)


def git(gd, *args):
    return subprocess.run(
        ["git", "-C", gd, *args], check=True, stdout=subprocess.PIPE, env=GIT_ENV
    ).stdout.decode()


class Blobs:
    """Reads blobs through one `git cat-file --batch` per epoch."""

    def __init__(self):
        self.procs = {}

    def get(self, gd, oid):
        p = self.procs.get(gd)
        if p is None:
            p = self.procs[gd] = subprocess.Popen(
                ["git", "-C", gd, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )
        p.stdin.write(oid.encode() + b"\n")
        p.stdin.flush()
        hdr = p.stdout.readline().split()
        if len(hdr) < 3 or hdr[1] != b"blob":
            return None
        data = p.stdout.read(int(hdr[2]))
        p.stdout.read(1)
        return data


def stream_blobs(gd, oids):
    """Yields (oid, bytes or None) for every oid, in order."""
    p = subprocess.Popen(["git", "-C", gd, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def feed():
        try:
            for oid in oids:
                p.stdin.write(oid.encode() + b"\n")
        finally:
            p.stdin.close()

    t = threading.Thread(target=feed, daemon=True)
    t.start()
    for oid in oids:
        hdr = p.stdout.readline().split()
        data = None
        if len(hdr) >= 3 and hdr[1] == b"blob":
            data = p.stdout.read(int(hdr[2]))
            p.stdout.read(1)
        yield oid, data
    t.join()
    p.wait()


# ---- message parsing ------------------------------------------------------

MID_RE = re.compile(r"<([^<>\s]+)>")
REPLY_RE = re.compile(r"^\s*(?:(?:re|aw|sv|fwd?|antw)\s*(?:\[\d+\])?\s*:\s*)+", re.I)
TAGS_RE = re.compile(r"^\s*((?:\[[^\]]*\]\s*)+)")
DIFF_META = (
    "index ", "new file mode", "deleted file mode", "old mode", "new mode", "similarity index",
    "dissimilarity index", "rename from", "rename to", "copy from", "copy to", "Binary files",
    "GIT binary patch", "literal ", "delta ",
)


def decode(data, charset=None):
    if charset:
        try:
            return data.decode(charset, "replace")
        except LookupError:
            pass
    return data.decode("utf-8", "replace")


def dehdr(v):
    if "=?" not in v:
        return v
    try:
        return str(email.header.make_header(email.header.decode_header(v)))
    except Exception:
        return v


def headers(raw):
    """({lowercased name: [unfolded values]}, body offset), without MIME parsing."""
    end = raw.find(b"\n\n")
    at = end + 2 if end >= 0 else len(raw)
    h, cur = {}, None
    for line in decode(raw[:at]).split("\n"):
        if line[:1] in (" ", "\t"):
            if cur:
                cur[-1] += " " + line.strip()
        elif ":" in line:
            k, v = line.split(":", 1)
            cur = h.setdefault(k.strip().lower(), [])
            cur.append(v.strip())
    return h, at


def first(h, k):
    v = h.get(k)
    return v[0] if v else ""


def body_text(raw, h, at):
    """All text/plain (and attached patch) parts, decoded."""
    ctype = first(h, "content-type").lower()
    cte = first(h, "content-transfer-encoding").lower()
    if not ctype.startswith("multipart") and cte in ("", "7bit", "8bit", "binary"):
        if ctype and not ctype.startswith("text/"):
            return ""
        m = re.search(r'charset\s*=\s*"?([\w.:-]+)', ctype)
        return decode(raw[at:], m and m[1])
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    text, html = [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ct = part.get_content_type()
        fn = (part.get_filename() or "").lower()
        if ct == "text/plain" or "patch" in ct or "diff" in ct or fn.endswith((".patch", ".diff")):
            text.append(decode(part.get_payload(decode=True) or b"", part.get_content_charset()))
        elif ct == "text/html":
            html.append(decode(part.get_payload(decode=True) or b"", part.get_content_charset()))
    if not text:
        text = [re.sub(r"<[^>]+>", " ", x) for x in html]
    return "\n".join(text)


def diff_path(s):
    s = s.split("\t", 1)[0].strip()
    if s == "/dev/null":
        return None
    return s[2:] if s[:2] in ("a/", "b/") else s


def split_body(text, cap=65536, dcap=131072):
    """-> (unquoted prose, diff file names, removed lines, added lines), each capped."""
    body, dfn, dfa, dfb = [], {}, [], []
    nb = na = nd = 0
    lines = text.split("\n")
    in_diff = False
    for i, line in enumerate(lines):
        if in_diff:
            c = line[:1]
            if c == "+":
                if line.startswith("+++ "):
                    if p := diff_path(line[4:]):
                        dfn[p] = 1
                elif nd < dcap:
                    dfb.append(line[1:])
                    nd += len(line)
                continue
            if c == "-":
                if line.startswith("--- "):
                    if p := diff_path(line[4:]):
                        dfn[p] = 1
                elif line == "-- ":
                    in_diff = False
                elif na < dcap:
                    dfa.append(line[1:])
                    na += len(line)
                continue
            if c in (" ", "@", "\\", "") or line.startswith(DIFF_META):
                continue
            if not line.startswith("diff "):
                in_diff = False
        if line.startswith("diff --git "):
            in_diff = True
            if m := re.match(r"a/(\S+) b/(\S+)", line[11:]):
                dfn[m[1]] = dfn[m[2]] = 1
            continue
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            in_diff = True
            if p := diff_path(line[4:]):
                dfn[p] = 1
            continue
        if line.lstrip()[:1] == ">":
            continue
        if nb < cap:
            body.append(line)
            nb += len(line) + 1
    return "\n".join(body), "\n".join(dfn), "\n".join(dfa), "\n".join(dfb)


def lead_tags(subject):
    m = TAGS_RE.match(REPLY_RE.sub("", subject))
    return m[1] if m else ""


def kind_of(subject, has_diff):
    if REPLY_RE.match(subject):
        return "r"
    tags = lead_tags(subject).upper()
    if "PULL" in tags:
        return "g"
    if re.search(r"\b0+/\d+\b", tags):
        return "c"
    return "p" if has_diff else "o"


def norm_subject(subject):
    s = TAGS_RE.sub("", REPLY_RE.sub("", subject))
    return " ".join(s.lower().split())


def msg_date(v, rt):
    try:
        t = email.utils.parsedate_tz(v)
        d = email.utils.mktime_tz(t) if t else rt
    except (TypeError, ValueError, OverflowError):
        d = rt
    return rt if d > rt + 86400 else d


def ids(h):
    """-> (mid or None, irt, tid)"""
    v = first(h, "message-id")
    mid = (MID_RE.findall(v) or [v.strip("<> ")])[0] or None
    refs = MID_RE.findall(" ".join(h.get("references", [])))
    irt = (MID_RE.findall(first(h, "in-reply-to")) or refs[-1:] or [None])[0]
    if irt == mid:
        irt = None
    return mid, irt, (refs[0] if refs and refs[0] != mid else irt) or mid


def parse_item(item):
    """Worker: (commit, rt, blob, deleted, raw) -> index tuple, or ("e", commit, error)."""
    try:
        return parse_msg(*item)
    except Exception as e:
        return ("e", item[0], f"blob {item[2]}: {e!r}")


def parse_msg(commit, rt, blob, deleted, raw):
    raw = (raw or b"").replace(b"\r\n", b"\n")
    h, at = headers(raw)
    mid, irt, tid = ids(h)
    mid = mid or f"{blob}@lore-mirror.invalid"
    tid = tid or mid
    if deleted:
        return ("d", commit, mid)
    subject = dehdr(first(h, "subject"))
    b, dfn, dfa, dfb = split_body(body_text(raw[: 8 << 20], h, at))
    return (
        "m", commit, blob, mid, msg_date(first(h, "date"), rt), rt,
        dehdr(first(h, "x-original-from") or first(h, "from")), subject,
        irt, tid, kind_of(subject, bool(dfn or dfb or dfa)),
        dehdr(", ".join(h.get("to", []))), dehdr(", ".join(h.get("cc", []))), b, dfn, dfa, dfb,
    )


# ---- indexing -------------------------------------------------------------


def index_epoch(db, lst, n, log):
    gd = gitdir(lst, n)
    row = db.execute("SELECT tip FROM epoch WHERE list = ? AND n = ?", (lst, n)).fetchone()
    tip = row[0] if row else None
    head = git(gd, "rev-parse", "-q", "--verify", "HEAD").strip()
    if not head or head == tip:
        return 0
    if tip and subprocess.run(["git", "-C", gd, "merge-base", "--is-ancestor", tip, head]).returncode:
        log(f"{lst}/{n}: history was rewritten, rescanning the epoch")
        tip = None
    out = git(
        gd, "log", "--reverse", "--root", "--raw", "--no-abbrev", "--no-renames",
        "--format=%x00%H %ct", f"{tip}..{head}" if tip else head,
    )
    todo = []
    for chunk in out.split("\0")[1:]:
        lines = chunk.split("\n")
        commit, ct = lines[0].split()
        for line in lines[1:]:
            if line.startswith(":"):
                meta, path = line.split("\t", 1)
                f = meta.split()
                if path in ("m", "d") and f[4] in ("A", "M"):
                    todo.append((commit, int(ct), f[3], path == "d"))
    if not todo:
        set_tip(db, lst, n, head)
        return 0

    bulk = len(todo) > 5000
    db.commit()
    db.execute(f"PRAGMA synchronous = {'OFF' if bulk else 'NORMAL'}")
    stream = zip(todo, stream_blobs(gd, [t[2] for t in todo]))
    pool = multiprocessing.get_context("fork").Pool(min(12, os.cpu_count() or 1)) if bulk else None
    added = done = 0
    t0 = time.time()
    try:
        while chunk := [(*t, data) for t, (_, data) in itertools.islice(stream, 10000)]:
            for r in pool.map(parse_item, chunk, chunksize=64) if pool else map(parse_item, chunk):
                if r[0] == "e":
                    log(f"{lst}/{n}: skipped unparsable {r[2]}")
                elif r[0] == "d":
                    drop(db, lst, r[2])
                else:
                    added += insert(db, lst, n, r)
            done += len(chunk)
            set_tip(db, lst, n, chunk[-1][0])
            if bulk:
                log(f"{lst}/{n}: {done}/{len(todo)} ({done / (time.time() - t0):.0f}/s)")
        set_tip(db, lst, n, head)
    finally:
        if pool:
            pool.terminate()
    return added


def set_tip(db, lst, n, tip):
    db.execute(
        "INSERT INTO epoch (list, n, tip) VALUES (?, ?, ?) ON CONFLICT (list, n) DO UPDATE SET tip = excluded.tip",
        (lst, n, tip),
    )
    db.commit()


def insert(db, lst, n, r):
    _, _, blob, mid, date, rt, author, subject, irt, tid, kind, to, cc, b, dfn, dfa, dfb = r
    row = db.execute("SELECT id FROM msg WHERE mid = ?", (mid,)).fetchone()
    new = row is None
    if new:
        rid = db.execute(
            "INSERT INTO msg (mid, date, rt, author, subject, irt, tid, kind, list, epoch, blob) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, date, rt, author, subject, irt, tid, kind, lst, n, blob),
        ).lastrowid
        db.execute(
            "INSERT INTO fts (rowid, s, f, t, c, b, dfn, dfa, dfb) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, subject, author, to, cc, b, dfn, dfa, dfb),
        )
    else:
        rid = row[0]
    db.execute("INSERT OR IGNORE INTO ml (list, id) VALUES (?, ?)", (lst, rid))
    return int(new)


def drop(db, lst, mid):
    row = db.execute("SELECT id FROM msg WHERE mid = ?", (mid,)).fetchone()
    if not row:
        return
    db.execute("DELETE FROM ml WHERE list = ? AND id = ?", (lst, row[0]))
    if not db.execute("SELECT 1 FROM ml WHERE id = ?", row).fetchone():
        db.execute("DELETE FROM msg WHERE id = ?", row)
        db.execute("DELETE FROM fts WHERE rowid = ?", row)


# ---- sync -----------------------------------------------------------------


def manifest():
    """{list: {epoch: fingerprint}} from lore's grokmirror manifest."""
    req = urllib.request.Request(f"{LORE}/manifest.js.gz", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(gzip.decompress(r.read()))
    out = {}
    for k, v in data.items():
        if m := re.fullmatch(r"/([^/]+)/git/(\d+)\.git", k):
            out.setdefault(m[1], {})[int(m[2])] = v.get("fingerprint")
    return out


def fetch(lst, n):
    gd = gitdir(lst, n)
    if os.path.isdir(gd):
        subprocess.run(["git", "-C", gd, "fetch", "--quiet", "--prune"], check=True, env=GIT_ENV)
        return "fetched"
    tmp = gd + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(gd), exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", "--mirror", f"{LORE}/{lst}/{n}", tmp], check=True, env=GIT_ENV)
    os.rename(tmp, gd)
    return "cloned"


def cmd_sync(a):
    try:
        os.makedirs(ROOT, exist_ok=True)
        lock = open(os.path.join(ROOT, ".sync.lock"), "w")
    except OSError as e:
        die(f"sync needs write access to {ROOT} and the network ({e.strerror}); "
            "in a sandbox, request escalation or rely on lore-sync.timer")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if a.wait else fcntl.LOCK_NB))
    except BlockingIOError:
        print("lore: another sync is running (use --wait)", file=sys.stderr)
        return 0
    log = (lambda s: None) if a.quiet else (lambda s: print(s, flush=True))
    lists = a.lists or configured()
    if not lists:
        die(f"no lists configured in {LISTS}")
    db = connect(write=True)
    failed = 0
    if not a.no_fetch:
        try:
            remote = manifest()
        except Exception as e:
            log(f"manifest unavailable ({e}); fetching local epochs only")
            remote = {lst: {n: None for n in local_epochs(lst)} for lst in lists}
        stored = {(l, n): fp for l, n, fp in db.execute("SELECT list, n, fp FROM epoch")}
        jobs = []
        for lst in lists:
            if lst not in remote:
                log(f"{lst}: not on lore (see {LORE}/manifest.js.gz)")
                failed += 1
            for n, fp in sorted(remote.get(lst, {}).items()):
                if fp is None or fp != stored.get((lst, n)) or not os.path.isdir(gitdir(lst, n)):
                    jobs.append((lst, n, fp))
        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            futs = {ex.submit(fetch, lst, n): (lst, n, fp) for lst, n, fp in jobs}
            for f in concurrent.futures.as_completed(futs):
                lst, n, fp = futs[f]
                try:
                    what = f.result()
                except subprocess.CalledProcessError as e:
                    log(f"{lst}/{n}: git failed ({e.returncode})")
                    failed += 1
                    continue
                if what == "cloned":
                    log(f"{lst}/{n}: cloned")
                db.execute(
                    "INSERT INTO epoch (list, n, fp) VALUES (?, ?, ?) ON CONFLICT (list, n) DO UPDATE SET fp = excluded.fp",
                    (lst, n, fp),
                )
                db.commit()
    if not a.no_index:
        for lst in lists:
            for n in local_epochs(lst):
                t0 = time.time()
                if added := index_epoch(db, lst, n, log):
                    log(f"{lst}/{n}: +{added} messages ({time.time() - t0:.0f}s)")
    if not (a.lists or a.no_fetch or a.no_index):
        with open(os.path.join(ROOT, ".last-sync"), "w") as f:
            f.write(f"{int(time.time())} {'ok' if not failed else f'{failed} errors'}\n")
    return 1 if failed else 0


def cmd_add(a):
    remote = manifest()
    have = configured()
    new = []
    for lst in a.lists:
        if lst not in remote:
            die(f"{lst} is not a lore inbox; names are in {LORE}/manifest.js.gz")
        if lst not in have and lst not in new:
            new.append(lst)
    with open(LISTS, "a+") as f:
        f.seek(0)
        text = f.read()
        f.write("\n" if text and not text.endswith("\n") else "")
        for lst in new:
            f.write(f"{lst}\n")
    print(f"added {' '.join(new) or 'nothing'} to {LISTS}")
    if new and not a.no_sync:
        return cmd_sync(argparse.Namespace(lists=new, wait=True, quiet=False, no_fetch=False, no_index=False))
    return 0


def cmd_status(a):
    db = connect()
    print(f"{ROOT}: index {os.path.getsize(DB) / 2**30:.1f} GiB, {db.execute('SELECT count(*) FROM msg').fetchone()[0]:,} unique messages")
    try:
        t, state = open(os.path.join(ROOT, ".last-sync")).read().split(" ", 1)
        print(f"last sync {fmt_time(int(t))} UTC ({(time.time() - int(t)) / 60:.0f} min ago): {state.strip()}")
    except (FileNotFoundError, ValueError):
        print("never synced")
    print(f"{'list':20} {'epochs':>6} {'messages':>10}  newest")
    for lst in configured():
        count = db.execute("SELECT count(*) FROM ml WHERE list = ?", (lst,)).fetchone()[0]
        newest = db.execute("SELECT max(date) FROM msg WHERE id = (SELECT max(id) FROM ml WHERE list = ?)", (lst,)).fetchone()[0]
        print(f"{lst:20} {len(local_epochs(lst)):>6} {count:>10,}  {fmt_time(newest) if newest else '-'}")


# ---- queries --------------------------------------------------------------


class QueryError(Exception):
    pass


FTS_COLS = {
    "": "{s b dfn}", "s": "s", "f": "f", "t": "t", "c": "c", "tc": "{t c}", "a": "{f t c}",
    "b": "b", "nq": "b", "bs": "{s b}", "dfn": "dfn", "dfa": "dfa", "dfb": "dfb",
    "dfab": "{dfa dfb}", "diff": "{dfn dfa dfb}",
}
SQL_PFX = {"m", "mid", "id", "l", "d", "rt", "is"}
SNIPPET_PFX = {"", "b", "nq", "bs", "dfn", "dfa", "dfb", "dfab", "diff"}
ALIASES = {
    "devicetree": "linux-devicetree", "dt": "linux-devicetree", "lakml": "linux-arm-kernel",
    "msm": "linux-arm-msm", "arm-msm": "linux-arm-msm", "qcom": "linux-arm-msm",
    "linux-kernel": "lkml", "alsa": "alsa-devel", "dri": "dri-devel",
}
KINDS = {"patch": "msg.kind = 'p'", "cover": "msg.kind = 'c'", "reply": "msg.kind = 'r'",
         "pull": "msg.kind = 'g'", "root": "msg.mid = msg.tid"}
UNITS = {"s": 1, "sec": 1, "second": 1, "min": 60, "minute": 60, "h": 3600, "hour": 3600,
         "d": 86400, "day": 86400, "w": 604800, "week": 604800, "m": 2629746, "mo": 2629746,
         "month": 2629746, "y": 31556952, "year": 31556952}
TOKEN_RE = re.compile(r'\s*(?:(\()|(\))|(-)?(?:([a-z]+):)?(?:"([^"]*)"|([^\s()"]+))(\*)?)')


def tokenize(q):
    toks, pos = [], 0
    q = q.strip()
    while pos < len(q):
        m = TOKEN_RE.match(q, pos)
        if not m or m.end() == pos:
            raise QueryError(f"cannot parse near {q[pos:]!r}")
        pos = m.end()
        lp, rp, neg, pfx, quoted, word, star = m.groups()
        if lp or rp:
            toks.append((lp or rp,))
            continue
        pfx = pfx or ""
        if quoted is None and not pfx and word in ("AND", "OR", "NOT"):
            toks.append(("op", word))
            continue
        val = quoted if quoted is not None else word
        if quoted is None and val.endswith("*"):
            val, star = val.rstrip("*"), "*"
        if pfx not in FTS_COLS and pfx not in SQL_PFX:
            val, pfx = f"{pfx}:{val}", ""
        term = ("term", pfx, val, bool(star))
        toks.append(("not", term) if neg else term)
    return toks


def parse_query(q):
    toks = tokenize(q)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def expr():
        nodes = [conj()]
        while peek() == ("op", "OR"):
            take()
            nodes.append(conj())
        return nodes[0] if len(nodes) == 1 else ("or", nodes)

    def conj():
        nodes = [unary()]
        while peek() not in (None, ("op", "OR"), (")",)):
            if peek() == ("op", "AND"):
                take()
            nodes.append(unary())
        return nodes[0] if len(nodes) == 1 else ("and", nodes)

    def unary():
        if peek() == ("op", "NOT"):
            take()
            return ("not", unary())
        t = take() if peek() else None
        if t == ("(",):
            e = expr()
            if peek() != (")",):
                raise QueryError("unbalanced parentheses")
            take()
            return e
        if t is None or t[0] not in ("term", "not"):
            raise QueryError("expected a term")
        return t

    if not toks:
        raise QueryError("empty query")
    e = expr()
    if peek() is not None:
        raise QueryError("unbalanced parentheses")
    return e


def fts_phrase(pfx, val, star, hl):
    if not re.search(r"\w", val):
        raise QueryError(f"term {val!r} has no searchable characters")
    if hl is not None and pfx in SNIPPET_PFX:
        hl.append(val)
    return f'{FTS_COLS[pfx]} : "{val.replace(chr(34), chr(34) * 2)}"' + (" *" if star else "")


def fts_expr(node, hl):
    if node[0] == "term":
        if node[1] in SQL_PFX:
            raise QueryError(f"{node[1]}: only works as a top-level AND term")
        return fts_phrase(node[1], node[2], node[3], hl)
    if node[0] == "or":
        return " OR ".join(f"({fts_expr(n, hl)})" for n in node[1])
    if node[0] == "and":
        pos = [n for n in node[1] if n[0] != "not"]
        if not pos:
            raise QueryError("a group needs at least one positive term")
        e = " AND ".join(f"({fts_expr(n, hl)})" for n in pos)
        return e + "".join(f" NOT ({fts_expr(n[1], None)})" for n in node[1] if n[0] == "not")
    raise QueryError("NOT needs a positive term beside it")


def when(s, end):
    s = s.strip().lower()
    if not s:
        return None
    if s == "now":
        return time.time()
    if s in ("today", "yesterday"):
        t = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        t -= 86400 if s == "yesterday" else 0
        return t + 86400 if end else t
    if (m := re.fullmatch(r"(\d+)\.?([a-z]+?)s?(?:\.ago)?", s)) and m[2] in UNITS:
        return time.time() - int(m[1]) * UNITS[m[2]]
    if m := re.fullmatch(r"(\d{4})(?:-?(\d\d)(?:-?(\d\d))?)?", s):
        y, mo, d = int(m[1]), int(m[2] or 1), int(m[3] or 1)
        start = datetime(y, mo, d, tzinfo=timezone.utc).timestamp()
        if not end:
            return start
        if m[3]:
            return start + 86400
        if m[2]:
            return datetime(y + mo // 12, mo % 12 + 1, 1, tzinfo=timezone.utc).timestamp()
        return datetime(y + 1, 1, 1, tzinfo=timezone.utc).timestamp()
    raise QueryError(f"bad date {s!r}: use YYYY[-MM[-DD]], YYYYMMDD, N.days.ago (or 2w, 3mo, 1y), today, yesterday")


def list_name(v):
    v = re.sub(r"[@.].*$", "", v.strip().lower())
    return ALIASES.get(v, v)


def sql_term(node, neg):
    _, pfx, val, _ = node
    no = "NOT " if neg else ""
    if pfx in ("m", "mid", "id"):
        return f"msg.mid {'!=' if neg else '='} ?", [norm_mid(val)]
    if pfx == "l":
        names = [list_name(x) for x in val.split(",") if x.strip()]
        missing = [x for x in names if x not in configured()]
        if missing:
            print(f"lore: not mirrored: {' '.join(missing)} (see `lore status`)", file=sys.stderr)
        return f"msg.id {no}IN (SELECT id FROM ml WHERE list IN ({','.join('?' * len(names))}))", names
    if pfx in ("d", "rt"):
        v = val.strip().lower()
        if ".." in v:
            a, b = v.split("..", 1)
            lo, hi = when(a, False), when(b, True)
        elif v in ("today", "yesterday") or re.fullmatch(r"\d{4}(-?\d\d(-?\d\d)?)?", v):
            lo, hi = when(v, False), when(v, True)
        else:
            lo, hi = when(v, False), None
        col = "msg.date" if pfx == "d" else "msg.rt"
        c = ([f"{col} >= {int(lo)}"] if lo is not None else []) + ([f"{col} < {int(hi)}"] if hi is not None else [])
        return f"{no}({' AND '.join(c) or '1'})", []
    if val.lower() not in KINDS:
        raise QueryError(f"is: takes {', '.join(KINDS)}")
    return f"{no}({KINDS[val.lower()]})", []


def compile_query(q):
    """-> (FTS5 MATCH expression or None, SQL clauses, params, snippet terms)"""
    ast = parse_query(q)
    conj = ast[1] if ast[0] == "and" else [ast]
    pos, neg, where, params, hl = [], [], [], [], []
    for c in conj:
        inner = c[1] if c[0] == "not" else c
        if inner[0] == "term" and inner[1] in SQL_PFX:
            clause, p = sql_term(inner, c[0] == "not")
            where.append(clause)
            params += p
        elif c[0] == "not":
            neg.append(fts_expr(inner, None))
        else:
            pos.append(fts_expr(c, hl))
    expr = None
    if pos:
        expr = " AND ".join(f"({e})" for e in pos) + "".join(f" NOT ({e})" for e in neg)
    else:
        for e in neg:
            where.append("msg.id NOT IN (SELECT rowid FROM fts WHERE fts MATCH ?)")
            params.append(e)
    return expr, where, params, hl


def norm_mid(s):
    s = s.strip()
    if re.match(r"https?://", s):
        parts = [urllib.parse.unquote(p) for p in urllib.parse.urlsplit(s).path.split("/")]
        s = next((p for p in parts if "@" in p), parts[-1] if parts else s)
    s = re.sub(r"^(?:id|mid|m):", "", s)
    return s.strip().strip("<>")


# ---- output ---------------------------------------------------------------


def fmt_time(t, fmt="%Y-%m-%d %H:%M"):
    return time.strftime(fmt, time.gmtime(t))


def who(author):
    name, addr = email.utils.parseaddr(author)
    return name or addr or author


def line(r, mark=" ", indent=0):
    return f"{mark}{'  ' * indent}{fmt_time(r.date, '%Y-%m-%d')} {who(r.author)}: {r.subject}  <{r.mid}>"


def lore_url(mid):
    return f"{LORE}/r/{urllib.parse.quote(mid, safe='@!$&()*+,;=:~')}"


def lists_of(db, rid):
    return [x for (x,) in db.execute("SELECT list FROM ml WHERE id = ?", (rid,))]


def load(db, mid):
    r = db.execute(f"SELECT {COLS} FROM msg WHERE mid = ?", (norm_mid(mid),)).fetchone()
    if not r:
        die(
            f"<{norm_mid(mid)}> is not in the local index. It may be newer than the last sync "
            f"(`lore sync`), or on a list that is not mirrored: try `b4 mbox -o - {norm_mid(mid)}`."
        )
    return Row(*r)


def raw_of(blobs, r):
    data = blobs.get(gitdir(r.list, r.epoch), r.blob)
    if data is None:
        die(f"blob {r.blob} for <{r.mid}> is missing from {gitdir(r.list, r.epoch)}")
    return data.replace(b"\r\n", b"\n")


def trim_quotes(text, keep):
    """Keeps the last `keep` lines of each quoted block; drops a trailing one."""
    out, block = [], []

    def flush(final):
        blank = 0
        while block and not block[-1].strip():
            block.pop()
            blank += 1
        if final and block:
            out.append(f"> [... {len(block)} quoted lines]")
        elif len(block) > keep:
            out.append(f"> [... {len(block) - keep} quoted lines]")
            out.extend(block[len(block) - keep:])
        else:
            out.extend(block)
        out.extend([""] * min(blank, 1))
        block.clear()

    for ln in text.split("\n"):
        if ln.lstrip()[:1] == ">" or (block and not ln.strip()):
            block.append(ln)
        else:
            if block:
                flush(False)
            out.append(ln)
    flush(True)
    return "\n".join(out).rstrip() + "\n"


def print_msg(db, r, raw, quotes, body=True, brief=False):
    h, at = headers(raw)
    for k in ("from", "date", "subject") if brief else ("from", "date", "subject", "to", "cc"):
        if v := h.get(k):
            print(f"{k.title()}: {dehdr(', '.join(v))}")
    print(f"Message-ID: <{r.mid}>")
    if r.irt:
        print(f"In-Reply-To: <{r.irt}>")
    print(f"Link: {lore_url(r.mid)}")
    print(f"Lists: {', '.join(lists_of(db, r.id))}")
    print()
    if body:
        text = body_text(raw, h, at)
        sys.stdout.write(trim_quotes(text, quotes) if quotes >= 0 else text.rstrip() + "\n")


def snippets(raw, terms, n=3):
    h, at = headers(raw)
    low = [t.lower() for t in terms]
    out = []
    for ln in body_text(raw, h, at).split("\n"):
        s = ln.strip()
        if s[:1] != ">" and any(t in s.lower() for t in low):
            out.append(s[:200])
            if len(out) == n:
                break
    return out


def cmd_search(a):
    try:
        expr, where, params, hl = compile_query(" ".join(a.query))
    except QueryError as e:
        die(f"bad query: {e}")
    if a.rank and not expr:
        die("--rank needs at least one text term")
    db = connect()
    frm = "msg JOIN fts ON fts.rowid = msg.id" if expr else "msg"
    w = (["fts MATCH ?"] if expr else []) + where
    p = ([expr] if expr else []) + params
    wsql = f" WHERE {' AND '.join(w)}" if w else ""
    order = "DESC" if not a.oldest else "ASC"
    try:
        if a.threads:
            rows = db.execute(
                f"SELECT msg.tid, max(msg.date), count(*) FROM {frm}{wsql} GROUP BY msg.tid ORDER BY max(msg.date) {order} LIMIT ?",
                (*p, a.limit),
            ).fetchall()
        else:
            key = "bm25(fts, 10.0, 3.0, 1.0, 1.0, 1.0, 3.0, 0.5, 0.5)" if a.rank else f"msg.date {order}"
            rows = [Row(*r) for r in db.execute(f"SELECT {COLS} FROM {frm}{wsql} ORDER BY {key} LIMIT ?", (*p, a.limit))]
    except sqlite3.OperationalError as e:
        die(f"query failed: {e} (FTS5 expression: {expr})")
    if not rows:
        print("lore: no matches", file=sys.stderr)
        return 1
    blobs = Blobs()
    for r in rows:
        if a.threads:
            tid, last, hits = r
            root = Row(*db.execute(f"SELECT {COLS} FROM msg WHERE tid = ? ORDER BY msg.mid = msg.tid DESC, date LIMIT 1", (tid,)).fetchone())
            size = db.execute("SELECT count(*) FROM msg WHERE tid = ?", (tid,)).fetchone()[0]
            if a.json:
                print(json.dumps({"tid": tid, "root": root.mid, "subject": root.subject, "from": root.author,
                                  "date": fmt_time(root.date), "last": fmt_time(last), "hits": hits, "messages": size}))
            else:
                print(f"{line(root).strip()}  [{hits} hits, {size} msgs, last {fmt_time(last, '%Y-%m-%d')}]")
            continue
        if a.json:
            print(json.dumps({"mid": r.mid, "date": fmt_time(r.date), "from": r.author, "subject": r.subject,
                              "kind": r.kind, "tid": r.tid, "lists": lists_of(db, r.id)}))
        else:
            print(line(r).strip())
        if a.context and hl:
            for s in snippets(raw_of(blobs, r), hl):
                print(f"    | {s}")
    return 0


def thread_rows(db, r, cap=5000):
    rows, want, done = {}, {r.mid, r.tid}, set()
    while (todo := list(want - done)[:300]) and len(rows) < cap:
        done.update(todo)
        ph = ",".join("?" * len(todo))
        for x in db.execute(f"SELECT {COLS} FROM msg WHERE mid IN ({ph}) OR tid IN ({ph}) OR irt IN ({ph})", todo * 3):
            x = Row(*x)
            if x.id not in rows:
                rows[x.id] = x
                want.update((x.mid, x.tid))
    return list(rows.values())


def tree(rows):
    """-> [(depth, row)] in thread order."""
    by_mid = {x.mid: x for x in rows}
    kids, roots = {}, []
    for x in sorted(rows, key=lambda x: x.date):
        parent = x.irt if x.irt in by_mid else (x.tid if x.tid in by_mid and x.tid != x.mid else None)
        (kids.setdefault(parent, []) if parent else roots).append(x)
    out, seen = [], set()
    for start in roots + sorted(rows, key=lambda x: x.date):
        stack = [(0, start)]
        while stack:
            d, x = stack.pop()
            if x.mid in seen:
                continue
            seen.add(x.mid)
            out.append((d, x))
            stack.extend((d + 1, k) for k in reversed(kids.get(x.mid, [])))
    return out


def cmd_thread(a):
    db = connect()
    r = load(db, a.mid)
    t = tree(thread_rows(db, r))
    if a.json:
        for d, x in t:
            print(json.dumps({"depth": d, "mid": x.mid, "date": fmt_time(x.date), "from": x.author,
                              "subject": x.subject, "kind": x.kind}))
        return 0
    dates = [x.date for _, x in t]
    print(f"# {len(t)} messages, {fmt_time(min(dates), '%Y-%m-%d')} .. {fmt_time(max(dates), '%Y-%m-%d')}")
    for d, x in t:
        print(line(x, "*" if x.mid == r.mid else " ", min(d, 12)))
    if not a.full:
        return 0
    blobs = Blobs()
    for i, (d, x) in enumerate(t, 1):
        print(f"\n===== [{i}/{len(t)}] depth {d} " + "=" * 40)
        skip = a.skip_patches and x.kind == "p"
        print_msg(db, x, raw_of(blobs, x), a.quotes, body=not skip, brief=True)
        if skip:
            print(f"[patch body omitted: lore show {x.mid}]")
    return 0


def cmd_show(a):
    db = connect()
    blobs = Blobs()
    for i, mid in enumerate(a.mid):
        r = load(db, mid)
        raw = raw_of(blobs, r)
        if a.raw:
            sys.stdout.flush()
            sys.stdout.buffer.write(raw)
            continue
        if i:
            print("\n" + "=" * 60)
        print_msg(db, r, raw, a.quotes)
    return 0


def cmd_mbox(a):
    db = connect()
    r = load(db, a.mid)
    rows = sorted(thread_rows(db, r), key=lambda x: x.date) if a.thread else [r]
    blobs = Blobs()
    out = sys.stdout.buffer
    for x in rows:
        raw = raw_of(blobs, x)
        out.write(b"From mboxrd@z Thu Jan  1 00:00:00 1970\n")
        for ln in (raw if raw.endswith(b"\n") else raw + b"\n").split(b"\n")[:-1]:
            out.write((b">" + ln if re.match(rb">*From ", ln) else ln) + b"\n")
        out.write(b"\n")
    return 0


def cmd_series(a):
    db = connect()
    r = load(db, a.mid)
    root = db.execute(f"SELECT {COLS} FROM msg WHERE mid = ?", (r.tid,)).fetchone()
    root = Row(*root) if root and Row(*root).kind in ("c", "p") else r
    norm = norm_subject(root.subject)
    words = re.findall(r"\w+", norm)
    if not words:
        die(f"cannot derive a series title from {root.subject!r}")
    found = {}
    q = f's : "{" ".join(words)}"'
    for x in db.execute(f"SELECT {COLS} FROM msg JOIN fts ON fts.rowid = msg.id WHERE fts MATCH ? AND msg.kind IN ('c', 'p')", (q,)):
        x = Row(*x)
        if norm_subject(x.subject) == norm:
            found[x.mid] = x
    raw = raw_of(Blobs(), root)
    if m := re.search(r"^change-id:\s*(\S+)", decode(raw), re.M):
        q = f'b : "{" ".join(re.findall(r"[^\W_]+", m[1]))}"'
        for x in db.execute(f"SELECT {COLS} FROM msg JOIN fts ON fts.rowid = msg.id WHERE fts MATCH ? AND msg.kind IN ('c', 'p')", (q,)):
            found.setdefault(x[1], Row(*x))
    for x in sorted(found.values(), key=lambda x: x.date):
        v = re.search(r"\bv(\d+)\b", lead_tags(x.subject), re.I)
        size = db.execute("SELECT count(*) FROM msg WHERE tid = ?", (x.tid,)).fetchone()[0]
        print(f"v{v[1] if v else 1:<3}{'*' if x.mid == root.mid else ' '} {line(x).strip()}  [{size} msgs in thread]")
    return 0


QUERY_HELP = """\
Query syntax (lore-style prefixes; terms are ANDed; AND, OR, NOT, -term, ( ) and
"quoted phrases" work; a trailing * matches a prefix):
  word "some phrase"   subject, unquoted body and diff file names
  s:  subject          f:  From            t: To    c: Cc   tc: To+Cc
  a:  any address      b:  unquoted body   bs: subject+body
  dfn: diff file name  dfb: added lines    dfa: removed lines   diff: all three
  m:  Message-ID       l:  list (linux-arm-msm, phone-devel, dt, lakml, ...; a,b = either)
  d:  date range       rt: received range  (2024-01-01..2024-06-30, 2.weeks.ago.., 3mo.., today)
  is: patch, cover, reply, pull or root (first message of a thread)
Punctuation splits tokens: s:"sm8550-mtp", dfn:arch/arm64/boot/dts/qcom/sdm845-*,
f:konradybcio@kernel.org and b:"qcom,sm6115-pinctrl" all match as phrases.
Quoted reply text is not indexed; search the original message instead.
Exit status is 1 when nothing matches.

Examples:
  lore search 'dfn:arch/arm64/boot/dts/qcom/sdm845-oneplus* d:1y..'
  lore search -t 's:"panel" is:patch l:phone-devel d:6mo..'
  lore search 'f:krzk b:"not a correct compatible"' --rank
  lore search -C 'dfb:"qcom,pmi8998-charger"'
"""


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="lore", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", aliases=["q"], help="search messages", epilog=QUERY_HELP,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=25, help="results (default 25)")
    p.add_argument("-t", "--threads", action="store_true", help="one line per matching thread")
    p.add_argument("-C", "--context", action="store_true", help="print matching body lines")
    p.add_argument("--rank", action="store_true", help="sort by relevance (BM25) instead of date")
    p.add_argument("--oldest", action="store_true", help="oldest first")
    p.add_argument("--json", action="store_true", help="JSON lines")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", help="print messages (Message-ID, <id>, or lore/patch.msgid.link URL)")
    p.add_argument("mid", nargs="+")
    p.add_argument("-q", "--quotes", type=int, default=-1, metavar="N",
                   help="keep only the last N lines of each quoted block (default: all)")
    p.add_argument("--raw", action="store_true", help="raw RFC 822 message")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("thread", help="thread tree around a message")
    p.add_argument("mid")
    p.add_argument("--full", action="store_true", help="also print every message")
    p.add_argument("-q", "--quotes", type=int, default=3, metavar="N",
                   help="with --full, quoted lines kept per block (default 3, -1 = all)")
    p.add_argument("--skip-patches", action="store_true", help="with --full, omit patch bodies")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_thread)

    p = sub.add_parser("mbox", help="mboxrd of a message or its thread (for b4 am -m - / git am)")
    p.add_argument("mid")
    p.add_argument("-t", "--thread", action="store_true", help="whole thread")
    p.set_defaults(func=cmd_mbox)

    p = sub.add_parser("series", help="other revisions of the series a message belongs to")
    p.add_argument("mid")
    p.set_defaults(func=cmd_series)

    p = sub.add_parser("sync", help="fetch mirrors from lore and update the index")
    p.add_argument("lists", nargs="*", help="default: every list in $LORE_DIR/lists")
    p.add_argument("--wait", action="store_true", help="wait for a running sync instead of skipping")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--no-index", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("add", help="mirror more lists")
    p.add_argument("lists", nargs="+")
    p.add_argument("--no-sync", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("status", help="mirrored lists, message counts, last sync")
    p.set_defaults(func=cmd_status)

    a = ap.parse_args()
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
