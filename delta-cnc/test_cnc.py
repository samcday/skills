import copy
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cnc


def row(tid="thread-a", **values):
    return {"id": tid, "name": "Example", "archived": False, **values}


def snapshot(host="desktop", rows=None):
    return {
        "schema": cnc.SCHEMA,
        "kind": "catalog",
        "source": {"hostname": host, "account_id": "account-a", "delta_path": "/bin/delta"},
        "captured_at": "2026-01-01T00:00:00+00:00",
        "exclude": [],
        "threads": cnc.catalog(rows if rows is not None else [row()], set()),
    }


class CatalogTests(unittest.TestCase):
    def test_ignores_indexes_and_preserves_unknown_optional_metadata(self):
        a = cnc.catalog([row(index=1)], set())
        b = cnc.catalog([row(index=99)], set())
        self.assertEqual(a, b)
        self.assertIsNone(a["thread-a"]["join_id"])
        self.assertIsNone(a["thread-a"]["status"])
        self.assertFalse(a["thread-a"]["catalog_archived"])
        self.assertNotIn("has_draft", a["thread-a"])

    def test_excludes_current_by_id_not_title(self):
        result = cnc.catalog([row("one"), row("two")], {"one"})
        self.assertEqual(set(result), {"two"})

    def test_schema_changes_and_duplicates_fail(self):
        for rows in ({}, [row(), row()], [{"id": "one"}], [row(archived=0)]):
            with self.subTest(rows=rows), self.assertRaises(cnc.Error):
                cnc.catalog(rows, set())


class ComparisonTests(unittest.TestCase):
    def test_detects_membership_and_catalog_archive_changes(self):
        left = snapshot(rows=[row(), row("left-only")])
        right = snapshot("laptop", [row(archived=True), row("right-only")])
        result = cnc.compare(left, right, "compare")
        self.assertEqual(result["counts"]["catalog_archive_disagreements"], 1)
        self.assertEqual(set(result["only_left"]), {"left-only"})
        self.assertEqual(set(result["only_right"]), {"right-only"})
        self.assertEqual(result["changed"]["thread-a"]["fields"], {
            "catalog_archived": {"left": False, "right": True}
        })

    def test_empty_catalog_is_valid(self):
        result = cnc.compare(snapshot(rows=[]), snapshot("laptop", []), "compare")
        self.assertEqual(result["counts"]["shared"], 0)

    def test_wrong_host_account_or_exclusions_fail(self):
        left = snapshot()
        with self.assertRaises(cnc.Error):
            cnc.compare(left, left, "compare")
        for key, value in (("account_id", "other"), ("hostname", "laptop"),
                           ("delta_path", "/other/delta")):
            right = copy.deepcopy(left)
            right["source"][key] = value
            with self.subTest(key=key), self.assertRaises(cnc.Error):
                cnc.compare(left, right, "diff")
        right = snapshot("laptop")
        right["exclude"] = ["thread-a"]
        with self.assertRaises(cnc.Error):
            cnc.compare(left, right, "compare")
        right["exclude"] = []
        right["source"]["account_id"] = "other"
        with self.assertRaises(cnc.Error):
            cnc.compare(left, right, "compare")

    def test_refresh_ignores_timing_and_metrics(self):
        left = snapshot()
        right = copy.deepcopy(left)
        right.update(captured_at="2026-01-02T00:00:00+00:00", metrics={"cli_calls": 2})
        self.assertEqual(cnc.compare(left, right, "diff")["changed"], {})

    def test_malformed_snapshot_fails_cleanly(self):
        for field, value in (("threads", []), ("source", None), ("exclude", "id"),
                             ("threads", {"one": {"catalog_archived": 0}})):
            invalid = snapshot()
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(cnc.Error):
                cnc.compare(snapshot(), invalid, "diff")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "invalid.json"
            path.write_text(json.dumps({**snapshot(), "threads": []}))
            with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(cnc.main(["diff", str(path), str(path)]), 2)
                self.assertNotIn("Traceback", stderr.getvalue())


class ParentTests(unittest.TestCase):
    def test_project_only_explicit_parent_state(self):
        data = {"name": "Parent", "buffer": "Not exported", "subthreads": [
            {"key": {"opaque": 1}, "value": {
                "name": "Same title", "archived": True, "kind": {"$variant": "Review"}
            }},
            {"key": {"opaque": 2}, "value": {
                "name": "Same title", "archived": False
            }},
        ]}
        result = cnc.project_parent(data)
        self.assertNotIn("buffer", result)
        self.assertEqual(len(result["children"]), 2)
        self.assertTrue(result["children"][0]["archived_in_parent"])
        self.assertFalse(result["children"][1]["archived_in_parent"])
        self.assertNotEqual(result["children"][0]["reference"],
                            result["children"][1]["reference"])

    def test_missing_state_is_not_empty_or_unarchived(self):
        for data in ({}, {"subthreads": None},
                     {"subthreads": [{"value": {"name": "Child"}}]},
                     {"subthreads": [{"value": {"name": "Child", "archived": True}}]},
                     {"subthreads": [{"key": None, "value": {"archived": True}}]}):
            with self.subTest(data=data), self.assertRaises(cnc.Error):
                cnc.project_parent(data)
        self.assertEqual(cnc.project_parent({"subthreads": []})["children"], [])

    def test_read_error_remains_unknown_and_duplicate_ids_read_once(self):
        reads = []

        class FakeDelta:
            def source(self):
                return {}

            def read(self, *args):
                reads.append(args[-1])
                if args[-1] == "bad-id":
                    raise cnc.Error("history reset")
                return {"subthreads": []}

            def metrics(self):
                return {}

        result = cnc.parents(FakeDelta(), ["good-id", "bad-id", "good-id"])
        self.assertEqual(set(result["threads"]), {"good-id", "bad-id"})
        self.assertEqual(result["threads"]["bad-id"], {"error": "history reset"})
        self.assertEqual(reads, ["good-id", "bad-id"])


class InvocationTests(unittest.TestCase):
    def test_explicit_relative_executable_is_not_a_path_lookup(self):
        self.assertEqual(cnc.Delta("./delta").executable, os.path.abspath("./delta"))
        with patch("cnc.shutil.which", return_value="/opt/custom/delta"):
            self.assertEqual(cnc.Delta("custom-delta").executable, "/opt/custom/delta")
        with patch("cnc.shutil.which", return_value=None), self.assertRaises(cnc.Error):
            cnc.Delta("missing-delta")

    @patch("cnc.subprocess.run")
    def test_snapshot_only_runs_auth_and_list(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, json.dumps({
                "account": {"account_id": "account-a", "token": "not stored"}
            }).encode(), b""),
            subprocess.CompletedProcess([], 0, json.dumps([row()]).encode(), b""),
        ]
        result = cnc.snapshot(cnc.Delta("/bin/delta"), [])
        self.assertEqual(result["metrics"]["cli_calls"], 2)
        self.assertNotIn("token", json.dumps(result))
        self.assertEqual(run.call_args_list[0].args[0],
                         ["/bin/delta", "auth", "status", "--json"])
        self.assertEqual(run.call_args_list[1].args[0],
                         ["/bin/delta", "thread", "list", "--json"])

    @patch("cnc.subprocess.run")
    def test_failed_auth_does_not_list(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"not signed in")
        with self.assertRaises(cnc.Error):
            cnc.snapshot(cnc.Delta("/bin/delta"), [])
        self.assertEqual(run.call_count, 1)

    @patch("cnc.subprocess.run")
    def test_timeouts_and_non_json_fail(self, run):
        run.side_effect = subprocess.TimeoutExpired("delta", 1)
        with self.assertRaises(cnc.Error):
            cnc.Delta("/bin/delta").read("thread", "list")
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess([], 0, b"not JSON", b"")
        with self.assertRaises(cnc.Error):
            cnc.Delta("/bin/delta").read("thread", "list")

    @patch("cnc.subprocess.run")
    def test_failed_stdout_never_becomes_a_diagnostic(self, run):
        payload = b'{"buffer":"private-transcript-sentinel' + b"x" * 100000 + b'"}'
        run.return_value = subprocess.CompletedProcess([], 1, payload, b"")
        delta = cnc.Delta("/bin/delta")
        with self.assertRaises(cnc.Error) as caught:
            delta.read("thread", "data", "thread-a")
        self.assertNotIn("private-transcript-sentinel", str(caught.exception))
        self.assertIn("stdout bytes withheld", str(caught.exception))
        self.assertEqual(delta.bytes, len(payload))
        self.assertLess(len(cnc.diagnostic(b"x" * 100000)), 2100)

    @patch("cnc.subprocess.run")
    def test_remote_partial_failure_preserves_unknown_and_exit_status(self, run):
        value = {
            "schema": cnc.SCHEMA, "kind": "parents", "source": {},
            "threads": {"thread-a": {"error": "history reset"}},
        }
        run.return_value = subprocess.CompletedProcess(
            [], 1, json.dumps(value).encode(), b""
        )
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = cnc.main(["parents", "--ssh", "host", "thread-a"])
        self.assertEqual(code, 1)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["threads"], value["threads"])
        self.assertNotIn("children", result["threads"]["thread-a"])

    @patch("cnc.subprocess.run")
    def test_remote_incompatible_output_is_a_clean_failure(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, b"[]", b"")
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(cnc.main(["snapshot", "--ssh", "host"]), 2)

    @patch("cnc.subprocess.run")
    def test_remote_setup_errors_do_not_emit_a_snapshot(self, run):
        for code, out, err in ((255, b"", b"offline"),
                               (0, b"unexpected banner", b""),
                               (2, b"", b"bad configuration")):
            run.return_value = subprocess.CompletedProcess([], code, out, err)
            with self.subTest(code=code), \
                    patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                    patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(cnc.main(["snapshot", "--ssh", "host"]), 2)
                self.assertEqual(stdout.getvalue(), "")

    def test_remote_quoting_and_no_recursive_ssh(self):
        args = cnc.arguments(["parents", "--ssh", "host", "--delta",
                             "/path with 'quote/delta", "thread-a"])
        argv = cnc.remote_args(args)
        command = cnc.shlex.split(argv[-1])
        self.assertEqual(command[0:3], ["python3", "-", "parents"])
        self.assertIn("/path with 'quote/delta", command)
        self.assertNotIn("--ssh", command)
        self.assertIn("-T", argv)
        self.assertEqual(argv[-2], "host")


if __name__ == "__main__":
    unittest.main()
