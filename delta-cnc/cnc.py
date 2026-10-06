#!/usr/bin/env python3
"""Small, read-only Delta catalog snapshots and selected parent inspection."""

import argparse
import datetime
import json
import os
import pathlib
import shlex
import shutil
import socket
import subprocess
import sys
import time


SCHEMA = 1


class Error(Exception):
    pass


def diagnostic(stderr):
    text = stderr.decode(errors="replace").strip()
    return text[:2048] + (" [stderr truncated]" if len(text) > 2048 else "")


class Delta:
    def __init__(self, executable=None, timeout=30):
        requested = os.path.expanduser(executable or "delta")
        candidate = requested if os.path.dirname(requested) else shutil.which(requested)
        if candidate is None:
            if executable:
                raise Error(f"Delta executable not found: {executable}")
            candidate = str(pathlib.Path.home() / ".local/delta.app/bin/delta")
        self.executable = os.path.abspath(candidate)
        self.timeout = timeout
        self.calls = 0
        self.bytes = 0
        self.started = time.monotonic()

    def read(self, *args):
        self.calls += 1
        try:
            result = subprocess.run(
                [self.executable, *args, "--json"],
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Error(f"delta {' '.join(args)}: {exc}") from exc
        self.bytes += len(result.stdout)
        if result.returncode:
            # Failed stdout can still contain a full, private thread export.
            detail = diagnostic(result.stderr) or "no stderr diagnostic"
            if result.stdout:
                detail += f" ({len(result.stdout)} stdout bytes withheld)"
            raise Error(f"delta {' '.join(args)} exited {result.returncode}: {detail}")
        try:
            return json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc:
            raise Error(f"delta {' '.join(args)} did not return JSON: {exc}") from exc

    def source(self):
        auth = self.read("auth", "status")
        account = auth.get("account") if isinstance(auth, dict) else None
        account_id = account.get("account_id") if isinstance(account, dict) else None
        if not isinstance(account_id, str) or not account_id:
            raise Error("auth status did not identify the account; no snapshot taken")
        return {
            "hostname": socket.gethostname(),
            "account_id": account_id,
            "delta_path": self.executable,
        }

    def metrics(self):
        return {
            "cli_calls": self.calls,
            "cli_stdout_bytes": self.bytes,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
        }


def envelope(kind, source):
    return {
        "schema": SCHEMA,
        "kind": kind,
        "source": source,
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def catalog(rows, exclude):
    if not isinstance(rows, list):
        raise Error("thread list schema changed: expected an array")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise Error("thread list schema changed: missing thread ID")
        tid = row["id"]
        if tid in exclude:
            continue
        if tid in result:
            raise Error(f"thread list contains duplicate ID {tid}")
        if not isinstance(row.get("archived"), bool):
            raise Error(f"{tid}: missing boolean catalog archive flag")
        # These are cached discovery fields, not a transcript revision or proof of
        # completion. Deliberately omit creation-order indexes and unknown fields.
        result[tid] = {
            key: row.get(key)
            for key in ("name", "join_id", "status", "created_at_utc",
                        "generated_title", "summary")
        }
        result[tid]["catalog_archived"] = row["archived"]
    return result


def snapshot(delta, exclude):
    result = envelope("catalog", delta.source())
    result["exclude"] = sorted(set(exclude))
    result["threads"] = catalog(delta.read("thread", "list"), set(exclude))
    result["metrics"] = delta.metrics()
    return result


def project_parent(data):
    if not isinstance(data, dict) or not isinstance(data.get("subthreads"), list):
        raise Error("thread data schema changed: no subthreads array; state unknown")
    children = []
    for entry in data["subthreads"]:
        child = entry.get("value") if isinstance(entry, dict) else None
        if (not isinstance(child, dict) or not isinstance(child.get("archived"), bool)
                or entry.get("key") is None):
            raise Error("thread data schema changed: child reference or archive state unknown")
        children.append({
            # Keep the native reference opaque. Names are not unique identifiers.
            "reference": entry["key"],
            "name": child.get("name"),
            "kind": child.get("kind"),
            "archived_in_parent": child["archived"],
        })
    return {"name": data.get("name"), "children": children}


def parents(delta, ids):
    result = envelope("parents", delta.source())
    result["threads"] = {}
    for tid in dict.fromkeys(ids):
        if not tid or tid.isdecimal() or any(
            not (c.isascii() and (c.isalnum() or c in "_-")) for c in tid
        ) or tid.startswith("-"):
            raise Error("parents requires discovered opaque thread IDs, not names or URLs")
        try:
            result["threads"][tid] = project_parent(delta.read("thread", "data", tid))
        except Error as exc:
            # An unreadable parent is not an empty parent or an unarchived child.
            result["threads"][tid] = {"error": str(exc)}
    result["metrics"] = delta.metrics()
    return result


def compare(left, right, mode):
    for value in (left, right):
        if (not isinstance(value, dict) or value.get("schema") != SCHEMA
                or value.get("kind") != "catalog"):
            raise Error("comparison requires two catalog snapshots from this helper")
        source, rows = value.get("source"), value.get("threads")
        if (not isinstance(source, dict) or any(
            not isinstance(source.get(key), str) or not source[key]
            for key in ("hostname", "account_id", "delta_path")
        )):
            raise Error("snapshot has an invalid source identity")
        if (not isinstance(value.get("exclude"), list)
                or any(not isinstance(tid, str) for tid in value["exclude"])
                or not isinstance(value.get("captured_at"), str)):
            raise Error("snapshot has invalid exclusions or capture time")
        if not isinstance(rows, dict) or any(
            not isinstance(tid, str) or not isinstance(row, dict)
            or not isinstance(row.get("catalog_archived"), bool)
            for tid, row in rows.items()
        ):
            raise Error("snapshot has an invalid catalog")
    a, b = left["source"], right["source"]
    if a["account_id"] != b["account_id"]:
        raise Error("different accounts: not a same-account thread comparison")
    if left["exclude"] != right["exclude"]:
        raise Error("different exclusion sets: recapture with the same --exclude IDs")
    if mode == "compare" and a["hostname"] == b["hostname"]:
        raise Error("both snapshots came from the same hostname; check the SSH targets")
    if mode == "diff" and a != b:
        raise Error("refresh diff requires the same hostname, account, and CLI path")

    lrows, rrows = left["threads"], right["threads"]
    common = lrows.keys() & rrows.keys()
    changed = {}
    for tid in sorted(common):
        fields = {
            key: {"left": lrows[tid].get(key), "right": rrows[tid].get(key)}
            for key in sorted(lrows[tid].keys() | rrows[tid].keys())
            if lrows[tid].get(key) != rrows[tid].get(key)
        }
        if fields:
            changed[tid] = {"name": rrows[tid].get("name"), "fields": fields}

    def labels(rows, ids):
        return {
            tid: {"name": rows[tid].get("name"),
                  "catalog_archived": rows[tid]["catalog_archived"]}
            for tid in sorted(ids)
        }

    return {
        "kind": "catalog_comparison",
        "mode": mode,
        "left": {"source": a, "captured_at": left["captured_at"]},
        "right": {"source": b, "captured_at": right["captured_at"]},
        "counts": {
            "left": len(lrows),
            "right": len(rrows),
            "shared": len(common),
            "only_left": len(lrows.keys() - rrows.keys()),
            "only_right": len(rrows.keys() - lrows.keys()),
            "catalog_archive_disagreements": sum(
                lrows[t]["catalog_archived"] != rrows[t]["catalog_archived"]
                for t in common
            ),
        },
        "only_left": labels(lrows, lrows.keys() - rrows.keys()),
        "only_right": labels(rrows, rrows.keys() - lrows.keys()),
        "changed": changed,
        "scope": "Cached catalog metadata only; not conversation or worktree equality.",
    }


def remote_args(args):
    command = ["python3", "-", args.command, "--timeout", str(args.timeout)]
    if args.delta:
        command += ["--delta", args.delta]
    if args.command == "snapshot":
        for tid in args.exclude:
            command += ["--exclude", tid]
    else:
        command += args.ids
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "--",
            args.ssh, shlex.join(command)]


def remote(args):
    started = time.monotonic()
    calls = 2 if args.command == "snapshot" else 1 + len(set(args.ids))
    # The same stdlib-only collector runs at the source. No remote installation,
    # SQLite access, or full thread JSON sent over SSH.
    result = subprocess.run(
        remote_args(args),
        input=pathlib.Path(__file__).read_bytes(),
        capture_output=True,
        timeout=calls * args.timeout + 15,
        check=False,
    )
    if not result.stdout:
        raise Error(diagnostic(result.stderr)
                    or f"SSH exited {result.returncode}")
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise Error("remote collector did not return JSON; check SSH/CLI setup") from exc
    kind = "catalog" if args.command == "snapshot" else "parents"
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("kind") != kind):
        raise Error("remote collector returned an incompatible report")
    if result.returncode not in (0, 1) or (
        result.returncode == 1 and value.get("kind") != "parents"
    ):
        raise Error(f"remote collector exited {result.returncode}")
    value["transport"] = {
        "ssh_target": args.ssh,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "stdout_bytes": len(result.stdout),
    }
    return value


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "parents"):
        sub = commands.add_parser(name)
        sub.add_argument("--ssh", metavar="HOST", help="collect on this SSH target")
        sub.add_argument("--delta", metavar="PATH", help="Delta executable on the source")
        sub.add_argument("--timeout", type=float, default=30,
                         help="seconds per Delta invocation (default: 30)")
        if name == "snapshot":
            sub.add_argument("--exclude", action="append", default=[], metavar="ID")
        else:
            sub.add_argument("ids", nargs="+", metavar="THREAD_ID")
    for name in ("diff", "compare"):
        sub = commands.add_parser(name)
        sub.add_argument("left", type=pathlib.Path)
        sub.add_argument("right", type=pathlib.Path)
    args = parser.parse_args(argv)
    if hasattr(args, "timeout") and not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be positive and finite")
    return args


def main(argv=None):
    args = arguments(argv)
    try:
        if args.command in ("diff", "compare"):
            value = compare(json.loads(args.left.read_text()),
                            json.loads(args.right.read_text()), args.command)
        elif args.ssh:
            value = remote(args)
        else:
            delta = Delta(args.delta, args.timeout)
            value = (snapshot(delta, args.exclude) if args.command == "snapshot"
                     else parents(delta, args.ids))
        print(json.dumps(value, indent=2, sort_keys=True))
        return int(value.get("kind") == "parents" and any(
            "error" in t for t in value["threads"].values()
        ))
    except (Error, OSError, ValueError, KeyError, TypeError,
            subprocess.TimeoutExpired) as exc:
        print(f"delta-cnc: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
