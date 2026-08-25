from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from fixture_factory import SyntheticTask, create_activity

from haifa_eval_viewer.catalog import ArchiveCatalog
from haifa_eval_viewer.server import ViewerServer


class ViewerServerTest(unittest.TestCase):
    def test_serves_loopback_api_and_security_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_activity(root, "run-api", [SyntheticTask("clean", "python", "PASS", 1.0, 0)])
            catalog = ArchiveCatalog([root], refresh_interval_seconds=0)
            server = ViewerServer(("127.0.0.1", 0), catalog)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/v1/activities?refresh=true") as response:
                    body = json.load(response)
                    self.assertEqual(1, body["activityCount"])
                    self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
                    self.assertEqual("no-store", response.headers["Cache-Control"])
                with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
                    self.assertIn(b"Haifa Eval Viewer", response.read())
                with urlopen(f"http://127.0.0.1:{server.server_port}/jsonl-viewer.html") as response:
                    standalone = response.read()
                    self.assertIn(b"JSONL Viewer", standalone)
                    self.assertIn(b"jsonl-normalizer.js", standalone)
                    self.assertIn(b"jsonl-viewer.js", standalone)
                with urlopen(f"http://127.0.0.1:{server.server_port}/jsonl-normalizer.js") as response:
                    self.assertIn(b"isHaifaRuntimeTrace", response.read())
                with urlopen(f"http://127.0.0.1:{server.server_port}/jsonl-viewer.js") as response:
                    self.assertIn(b"parseJsonl", response.read())
                task_url = f"http://127.0.0.1:{server.server_port}/api/v1/activities/run-api/tasks/clean__trial1"
                with urlopen(task_url) as response:
                    task = json.load(response)
                model_event = next(
                    event for event in task["trajectory"]["events"] if event["operation"] == "model.invoke"
                )
                raw_url = f"{task_url}/events/{quote(model_event['eventId'])}/raw"
                with urlopen(raw_url) as response:
                    raw = json.load(response)
                    self.assertEqual("must-not-appear", raw["raw"]["attributes"]["reasoning_content"])
                    self.assertEqual("no-store", response.headers["Cache-Control"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
