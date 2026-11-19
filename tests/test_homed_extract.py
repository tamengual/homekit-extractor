#!/usr/bin/env python3
"""Tests for homed_extract.py — Shortcut workflow decoding."""

import os
import plistlib
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from homed_extract import decode_shortcut_workflow


def workflow(*steps):
    return plistlib.dumps({"WFWorkflowActions": [
        {"WFWorkflowActionIdentifier": f"is.workflow.actions.{ident}",
         "WFWorkflowActionParameters": params}
        for ident, params in steps
    ]}, fmt=plistlib.FMT_BINARY)


class TestDecodeShortcutWorkflow(unittest.TestCase):
    def test_keeps_scalar_params_of_other_steps(self):
        steps = decode_shortcut_workflow(workflow(
            ("repeat.count", {"WFControlFlowMode": 0, "WFRepeatCount": 10.0,
                              "GroupingIdentifier": "abc"}),
            ("delay", {"WFDelayTime": 5.0, "UUID": "xyz"}),
            ("exit", {}),
        ))
        self.assertEqual(steps[0], {"type": "repeat.count",
                                    "params": {"WFControlFlowMode": 0, "WFRepeatCount": 10.0}})
        self.assertEqual(steps[1], {"type": "delay", "params": {"WFDelayTime": 5.0}})
        self.assertEqual(steps[2], {"type": "exit"})


if __name__ == "__main__":
    unittest.main()
