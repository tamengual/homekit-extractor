#!/usr/bin/env python3
"""Tests for merge_exports.py — multi-signal matching and canonical merge."""

import json
import os
import sys
import unittest

# Add scripts/ to path so we can import merge_exports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from merge_exports import (
    normalize_name,
    compute_match_score,
    find_best_matches,
    merge_into_canonical,
    merge_exports,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name), "r") as f:
        return json.load(f)


class TestNormalizeName(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(normalize_name("Hello World"), "hello world")

    def test_collapse_whitespace(self):
        self.assertEqual(normalize_name("Turn  Off   Lights"), "turn off lights")

    def test_strip_non_ascii(self):
        # Narrow no-break space (U+202F) and other non-ASCII should be removed
        self.assertEqual(normalize_name("10\u202f00\u202fPM"), "10 00 pm")

    def test_empty(self):
        self.assertEqual(normalize_name(""), "")
        self.assertEqual(normalize_name(None), "")

    def test_case_insensitive(self):
        self.assertEqual(
            normalize_name("Turn Off Lights at 11 PM"),
            normalize_name("turn off lights at 11 pm"),
        )


class TestComputeMatchScore(unittest.TestCase):
    def test_identical_automations_score_high(self):
        auto = {
            "name": "Turn Off Lights",
            "triggerType": "timer",
            "enabled": True,
            "actionSets": [{"actions": [{"actionType": "characteristicWrite"}]}],
            "events": [],
        }
        score = compute_match_score(auto, auto)
        self.assertGreater(score, 0.85)

    def test_different_names_score_low(self):
        a = {"name": "Turn Off Lights", "triggerType": "timer", "enabled": True}
        b = {"name": "Open Garage Door", "triggerType": "event", "enabled": False}
        score = compute_match_score(a, b)
        self.assertLess(score, 0.40)

    def test_same_name_different_trigger_still_matches(self):
        a = {"name": "Bedroom Button Single Press", "triggerType": "event", "enabled": True}
        b = {"name": "Bedroom Button Single Press", "triggerType": "", "enabled": True}
        score = compute_match_score(a, b)
        # Name match alone gives 0.50, should still be above threshold
        self.assertGreater(score, 0.55)

    def test_matching_trigger_boosts_score(self):
        base = {"name": "Test Auto", "enabled": True}
        a = {**base, "triggerType": "event"}
        b_same = {**base, "triggerType": "event"}
        b_diff = {**base, "triggerType": "timer"}
        score_same = compute_match_score(a, b_same)
        score_diff = compute_match_score(a, b_diff)
        self.assertGreater(score_same, score_diff)


class TestFindBestMatches(unittest.TestCase):
    def test_fixture_matching(self):
        app = load_fixture("app_export_minimal.json")
        homed = load_fixture("homed_export_minimal.json")

        matches, unmatched_app, unmatched_homed = find_best_matches(
            app["automations"], homed["automations"]
        )

        # Should match 4 automations (the 4 common ones)
        self.assertEqual(len(matches), 4)

        # Should have 0 unmatched app (all 4 app autos matched)
        self.assertEqual(len(unmatched_app), 0)

        # Should have 1 unmatched homed ("Homed-Only Automation")
        self.assertEqual(len(unmatched_homed), 1)

        # All matches should have high confidence
        for _, _, score in matches:
            self.assertGreater(score, 0.60)

    def test_no_duplicates_in_matches(self):
        app = load_fixture("app_export_minimal.json")
        homed = load_fixture("homed_export_minimal.json")

        matches, _, _ = find_best_matches(
            app["automations"], homed["automations"]
        )

        app_idxs = [m[0] for m in matches]
        homed_idxs = [m[1] for m in matches]

        # Each index should appear at most once
        self.assertEqual(len(app_idxs), len(set(app_idxs)))
        self.assertEqual(len(homed_idxs), len(set(homed_idxs)))


class TestMergeIntoCanonical(unittest.TestCase):
    def test_workflow_injection(self):
        app_auto = {
            "name": "Test Button",
            "isLikelyShortcut": True,
            "actionSets": [{"actions": [{"actionType": "shortcut"}]}],
        }
        homed_auto = {
            "name": "Test Button",
            "actionSets": [{
                "actions": [{
                    "actionType": "shortcut",
                    "workflowSteps": [
                        {"type": "conditional", "mode": "if"},
                        {"type": "conditional", "mode": "end"},
                    ],
                }],
            }],
        }

        merged = merge_into_canonical(app_auto, homed_auto)
        action = merged["actionSets"][0]["actions"][0]
        self.assertEqual(action["actionType"], "shortcut")
        self.assertEqual(len(action["workflowSteps"]), 2)

    def test_event_promotion(self):
        app_auto = {"name": "Test", "actionSets": []}
        homed_auto = {
            "name": "Test",
            "events": [{"eventType": "charValue", "characteristic": "Motion"}],
            "triggerType": "event",
            "actionSets": [],
        }

        merged = merge_into_canonical(app_auto, homed_auto)
        # Events should be promoted to canonical field, not _homed_events
        self.assertIn("events", merged)
        self.assertNotIn("_homed_events", merged)
        self.assertEqual(merged["triggerType"], "event")
        self.assertNotIn("_homed_triggerType", merged)

    def test_does_not_overwrite_existing_fields(self):
        app_auto = {
            "name": "Test",
            "triggerType": "timer",
            "events": [{"eventType": "time"}],
            "actionSets": [],
        }
        homed_auto = {
            "name": "Test",
            "triggerType": "event",
            "events": [{"eventType": "charValue"}],
            "actionSets": [],
        }

        merged = merge_into_canonical(app_auto, homed_auto)
        # App data should be preserved when it already has the field
        self.assertEqual(merged["triggerType"], "timer")
        self.assertEqual(merged["events"][0]["eventType"], "time")


class TestMergeExports(unittest.TestCase):
    def test_full_merge(self):
        app = load_fixture("app_export_minimal.json")
        homed = load_fixture("homed_export_minimal.json")

        merged, stats = merge_exports(app, homed)

        # Stats should be populated
        self.assertEqual(stats["app_automations"], 4)
        self.assertEqual(stats["homed_automations"], 5)
        self.assertEqual(stats["matched"], 4)
        self.assertGreater(stats["shortcut_workflows_injected"], 0)
        self.assertEqual(stats["unmatched_homed"], 1)

        # Merged data should have 5 automations (4 matched + 1 homed-only)
        self.assertEqual(len(merged["automations"]), 5)

        # The homed-only automation should be added
        homed_only = [a for a in merged["automations"] if a.get("_source") == "homed_only"]
        self.assertEqual(len(homed_only), 1)
        self.assertEqual(homed_only[0]["name"], "Homed-Only Automation")

        # Merge metadata should be present
        self.assertIn("_merge", merged)
        self.assertIn("matchStrategy", merged["_merge"])

        # No _homed_* sidecar fields should exist
        for auto in merged["automations"]:
            for key in auto:
                self.assertFalse(
                    key.startswith("_homed_"),
                    f"Found sidecar field '{key}' in automation '{auto.get('name')}'",
                )

    def test_shortcut_workflow_injected(self):
        app = load_fixture("app_export_minimal.json")
        homed = load_fixture("homed_export_minimal.json")

        merged, _ = merge_exports(app, homed)

        # Find the button press automation
        button_auto = next(
            a for a in merged["automations"]
            if a["name"] == "Bedroom Button Single Press"
        )

        # Should have workflow steps injected
        action = button_auto["actionSets"][0]["actions"][0]
        self.assertEqual(action["actionType"], "shortcut")
        self.assertIn("workflowSteps", action)
        self.assertEqual(len(action["workflowSteps"]), 5)


if __name__ == "__main__":
    unittest.main()
