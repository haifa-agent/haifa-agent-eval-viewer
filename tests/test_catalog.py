from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from fixture_factory import SyntheticTask, add_unsafe_primary_row, create_activity

from haifa_eval_viewer.catalog import ArchiveCatalog


class ArchiveCatalogTest(unittest.TestCase):
    def test_lists_latest_finalization_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            older = create_activity(root, "run-older", [SyntheticTask("older", "python", "PASS", 1.0, 0)])
            newer = create_activity(root, "run-newer", [SyntheticTask("newer", "python", "PASS", 1.0, 0)])
            older_marker = older / "integrity" / "finalization.json"
            newer_marker = newer / "integrity" / "finalization.json"
            os.utime(older_marker, (1_700_000_000, 1_700_000_000))
            os.utime(newer_marker, (1_800_000_000, 1_800_000_000))
            catalog = ArchiveCatalog([root], refresh_interval_seconds=0)

            activities = catalog.list_activities(force_refresh=True)["activities"]

            self.assertEqual(["run-newer", "run-older"], [item["activityId"] for item in activities])
            self.assertGreater(activities[0]["archivedAtUtc"], activities[1]["archivedAtUtc"])

    def test_ignores_legacy_inventory_without_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            integrity = root / "legacy" / "final" / "run-legacy" / "integrity"
            integrity.mkdir(parents=True)
            (integrity / "inventory.json").write_text(
                json.dumps({"schemaVersion": 1, "evalId": "legacy", "runId": "run-legacy"}),
                encoding="utf-8",
            )
            catalog = ArchiveCatalog([root], refresh_interval_seconds=0)

            activities = catalog.list_activities(force_refresh=True)

            self.assertEqual(0, activities["activityCount"])

    def test_ignores_non_complete_finalization_with_visible_catalog_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_activity(
                root,
                "run-blocked",
                [SyntheticTask("blocked", "python", "FAIL", 0.0, 2)],
                finalization_status="BLOCKED",
            )
            catalog = ArchiveCatalog([root], refresh_interval_seconds=0)

            activities = catalog.list_activities(force_refresh=True)

            self.assertEqual(0, activities["activityCount"])
            self.assertEqual("ACTIVITY_NOT_COMPLETE", activities["issues"][0]["code"])

    def test_discovers_new_archived_activity_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_activity(root, "run-one", [SyntheticTask("clean", "python", "PASS", 1.0, 0)])
            catalog = ArchiveCatalog([root], refresh_interval_seconds=0)

            first = catalog.list_activities(force_refresh=True)
            self.assertEqual(1, first["activityCount"])

            create_activity(
                root, "run-two", [SyntheticTask("later", "go", "FAIL", 0.0, 2, "NonZeroAgentExitCodeError")]
            )
            second = catalog.list_activities(force_refresh=True)
            self.assertEqual(2, second["activityCount"])
            self.assertEqual({"run-one", "run-two"}, {item["activityId"] for item in second["activities"]})

    def test_combines_verifier_and_agent_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_activity(
                root,
                "run-filter",
                [
                    SyntheticTask("clean-pass", "python", "PASS", 1.0, 0),
                    SyntheticTask(
                        "exception-pass", "go", "PASS", 1.0, 2, "NonZeroAgentExitCodeError", "TOOL_OUTCOME_UNKNOWN"
                    ),
                    SyntheticTask(
                        "model-fail", "rust", "FAIL", 0.0, 2, "NonZeroAgentExitCodeError", "MODEL_RESPONSE_INVALID"
                    ),
                ],
            )
            catalog = ArchiveCatalog([root], refresh_interval_seconds=0)
            filtered = catalog.list_tasks("run-filter", {"verifier": "PASS", "agent": "EXCEPTION"})

            self.assertEqual(3, filtered["total"])
            self.assertEqual(1, filtered["filtered"])
            self.assertEqual("aider/exception-pass", filtered["tasks"][0]["taskId"])
            self.assertEqual("TOOL_OUTCOME_UNKNOWN", filtered["tasks"][0]["errorCode"])

    def test_default_projection_redacts_private_fields_and_raw_event_exposes_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_activity(
                root,
                "run-error",
                [
                    SyntheticTask(
                        "broken", "cpp", "FAIL", 0.0, 2, "NonZeroAgentExitCodeError", "MODEL_RESPONSE_INVALID", True
                    )
                ],
            )
            catalog = ArchiveCatalog([root], refresh_interval_seconds=0)
            task = catalog.get_task("run-error", "broken__trial1")
            trajectory = task["trajectory"]
            focus = next(event for event in trajectory["events"] if event["eventId"] == trajectory["focusEventId"])

            self.assertEqual(1, trajectory["normalizerVersion"])
            self.assertEqual("MODEL_RESPONSE_INVALID", focus["errorCode"])
            self.assertTrue(any(event["category"] == "UNKNOWN" for event in trajectory["events"]))
            serialized = json.dumps(task)
            self.assertNotIn("reasoning_content", serialized)
            self.assertNotIn("private provider body", serialized)
            self.assertNotIn(str(root), serialized)

            model_event = next(event for event in trajectory["events"] if event["operation"] == "model.invoke")
            raw_model = catalog.get_raw_event("run-error", "broken__trial1", model_event["eventId"])
            self.assertEqual("must-not-appear", raw_model["raw"]["attributes"]["reasoning_content"])

            tool_event = next(event for event in trajectory["events"] if event["operation"] == "file.edit")
            raw_tool = catalog.get_raw_event("run-error", "broken__trial1", tool_event["eventId"])
            self.assertIn("complete tool arguments", raw_tool["raw"]["row"]["arguments_payload"])

    def test_browsing_does_not_modify_archive_and_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            activity = create_activity(root, "run-readonly", [SyntheticTask("safe", "java", "PASS", 1.0, 0)])
            database = activity / "jobs" / "primary" / "safe__trial1" / "agent" / "haifa-runtime.db"
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            add_unsafe_primary_row(activity)
            catalog = ArchiveCatalog([root], refresh_interval_seconds=0)
            tasks = catalog.list_tasks("run-readonly")
            unsafe = next(task for task in tasks["tasks"] if task["taskId"] == "aider/unsafe")
            after = hashlib.sha256(database.read_bytes()).hexdigest()

            self.assertEqual(before, after)
            self.assertEqual("ERROR", unsafe["dataQuality"]["status"])
            self.assertEqual(0, unsafe["trajectoryEventCount"])


if __name__ == "__main__":
    unittest.main()
