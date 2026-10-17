#!/usr/bin/env python3
"""Tests for homed_extract.py against synthetic databases numbered like different macOS releases."""

import json
import os
import plistlib
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "homed_extract.py")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import homed_extract  # noqa: E402
from homed_db import NUMBERING_MACOS27, NUMBERING_ORIGINAL, build_db  # noqa: E402


class HomedSchemaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "core.sqlite")

    def build(self, numbering, tweak=None, values="bplist"):
        build_db(self.db, numbering, values)
        if tweak:
            conn = sqlite3.connect(self.db)
            tweak(conn)
            conn.commit()
            conn.close()

    def extract(self, numbering, tweak=None, values="bplist"):
        self.build(numbering, tweak, values)
        return homed_extract.extract(self.db)

    @staticmethod
    def by_name(out, name):
        return next(a for a in out["automations"] if a["name"] == name)

    def assert_reference_automations(self, out):
        a1, a2 = self.by_name(out, "Automation 1"), self.by_name(out, "Automation 2")
        self.assertEqual(a1["triggerType"], "event")
        self.assertEqual([e["eventType"] for e in a1["events"]], ["charValue"])
        self.assertEqual([e["eventType"] for e in a2["events"]], ["charRange"])
        act = a1["actionSets"][0]["actions"][0]
        self.assertEqual((act["actionType"], act["accessoryName"]), ("characteristicWrite", "Lamp 1"))
        steps = a2["actionSets"][0]["actions"][0]["workflowSteps"]
        self.assertEqual([s["type"] for s in steps], ["conditional", "homeaccessory", "conditional", "conditional"])

    def test_macos27_numbering_names_trigger_and_event_types(self):
        out = self.extract(NUMBERING_MACOS27)
        a1, a2 = self.by_name(out, "Automation 1"), self.by_name(out, "Automation 2")
        self.assertEqual(a1["triggerType"], "event")
        self.assertEqual([e["eventType"] for e in a1["events"]], ["charValue"])
        self.assertEqual([e["eventType"] for e in a2["events"]], ["charRange"])

    def test_macos27_junction_links_action_sets(self):
        self.assert_reference_automations(self.extract(NUMBERING_MACOS27))

    def test_original_numbering_still_supported(self):
        self.assert_reference_automations(self.extract(NUMBERING_ORIGINAL))

    def test_range_event_exposes_minimum_and_maximum(self):
        ev = self.by_name(self.extract(NUMBERING_MACOS27), "Automation 2")["events"][0]
        self.assertEqual((ev["minimum"], ev["maximum"]), (18, 22))

    def test_keyed_archiver_bounds_are_unwrapped(self):
        archive = plistlib.dumps({"$archiver": "NSKeyedArchiver", "$version": 100000,
                                  "$top": {"root": plistlib.UID(1)}, "$objects": ["$null", 22.5]},
                                 fmt=plistlib.FMT_BINARY)
        out = self.extract(NUMBERING_MACOS27,
                           lambda c: c.execute("UPDATE ZMKFEVENT SET ZMAX = ? WHERE Z_PK = 2", (archive,)))
        self.assertEqual(self.by_name(out, "Automation 2")["events"][0]["maximum"], 22.5)

    def test_missing_required_entity_raises_schema_error(self):
        numbering = {k: v for k, v in NUMBERING_MACOS27.items() if k != "MKFShortcutAction"}
        with self.assertRaises(homed_extract.SchemaError):
            self.extract(numbering)
        output = os.path.join(self.tmp.name, "out.json")
        run = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--db", self.db, "-o", output],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 4, run.stderr)
        self.assertIn("Unsupported homed schema", run.stderr)
        self.assertFalse(os.path.exists(output))

    def test_missing_primarykey_table_is_a_schema_error(self):
        with self.assertRaises(homed_extract.SchemaError):
            self.extract(NUMBERING_MACOS27, lambda c: c.execute("DROP TABLE Z_PRIMARYKEY"))

    def test_unreadable_database_is_not_a_schema_error(self):
        with open(self.db, "w") as fh:
            fh.write("not a database\n" * 200)
        with self.assertRaises(sqlite3.DatabaseError) as caught:
            homed_extract.extract(self.db)
        self.assertNotIsInstance(caught.exception, homed_extract.SchemaError)

    def test_opack_values_are_decoded(self):
        out = self.extract(NUMBERING_MACOS27, values="opack")
        a1, a2 = self.by_name(out, "Automation 1"), self.by_name(out, "Automation 2")
        self.assertIs(a1["events"][0]["eventValue"], True)
        self.assertIs(a1["actionSets"][0]["actions"][0]["targetValue"], True)
        self.assertEqual((a2["events"][0]["minimum"], a2["events"][0]["maximum"]), (18.0, 22.0))

    def test_bplist_values_still_decoded(self):
        out = self.extract(NUMBERING_ORIGINAL)
        a1 = self.by_name(out, "Automation 1")
        self.assertIs(a1["events"][0]["eventValue"], True)
        self.assertIs(a1["actionSets"][0]["actions"][0]["targetValue"], True)

    def test_decode_opack_scalars_and_containers(self):
        d = homed_extract.decode_opack
        self.assertEqual([d(b"\x01"), d(b"\x02"), d(b"\x04")], [True, False, None])
        self.assertEqual([d(b"\x08"), d(b"\x09"), d(b"\x2f"), d(b"\x30\x64"), d(b"\x31\x10\x27")], [0, 1, 39, 100, 10000])
        self.assertAlmostEqual(d(b"\x35" + struct.pack("<f", 21.5)), 21.5)
        self.assertEqual(d(b"\x36" + struct.pack("<d", 0.25)), 0.25)
        self.assertEqual(d(b"\x43abc"), "abc")
        self.assertEqual(d(b"\xd2\x08\x09"), [0, 1])
        self.assertEqual(d(b"\xe1\x41a\x09"), {"a": 1})

    def test_undecodable_value_is_kept_as_hex(self):
        self.assertEqual(homed_extract.decode_value(b"\xff\x00"), "opack:ff00")

    def test_ambiguous_junction_raises_schema_error(self):
        with self.assertRaises(homed_extract.SchemaError):
            self.extract(NUMBERING_MACOS27, lambda c: c.execute(
                "CREATE TABLE Z_99TRIGGERS_ (Z_41ACTIONSETS_ INTEGER, Z_142TRIGGERS_ INTEGER)"))

    def test_decoy_table_with_one_matching_column_is_ignored(self):
        out = self.extract(NUMBERING_MACOS27, lambda c: c.execute(
            "CREATE TABLE Z_10DECOY_ (Z_41ACTIONSETS_ INTEGER, Z_7OTHERS_ INTEGER)"))
        self.assert_reference_automations(out)

    def test_entity_number_absent_from_primarykey_is_ent_n(self):
        out = self.extract(NUMBERING_MACOS27, lambda c: c.execute(
            "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER) VALUES (3, 777, 1)"))
        self.assertIn("ent_777", [e["eventType"] for e in self.by_name(out, "Automation 1")["events"]])

    def test_decode_opack_sized_strings_bytes_and_terminated_containers(self):
        d = homed_extract.decode_opack
        self.assertEqual(d(b"\x61\x03abc"), "abc")
        self.assertEqual(d(b"\x62\x03\x00abc"), "abc")
        self.assertEqual(d(b"\x91\x02\xab\xcd"), "hex:abcd")
        self.assertEqual(d(b"\xdf\x08\x09\x03"), [0, 1])
        self.assertEqual(d(b"\xef\x41a\x09\x03"), {"a": 1})

    def test_opack_dict_counts_pairs_not_distinct_keys(self):
        # two pairs whose keys collide once stringified: 1 and "1"
        self.assertEqual(homed_extract.decode_opack(b"\xe2\x09\x01\x411\x02"), {"1": False})

    def test_float32_noise_is_trimmed(self):
        self.assertEqual(homed_extract.decode_opack(b"\x35" + struct.pack("<f", 18.3)), 18.3)

    def test_corrupt_opack_falls_back_to_hex_instead_of_crashing(self):
        v = homed_extract.decode_value
        deep = b"\xd1" * 2000 + b"\x08"
        for blob in (deep, b"\x33\x01", b"\x08\x08", b"\x06" + struct.pack("<d", 1e300)):
            self.assertEqual(v(blob), "opack:" + blob.hex())

    def test_undecodable_bplist_is_kept_as_hex(self):
        self.assertEqual(homed_extract.decode_value(b"bplist00garbage"), "bplist:" + b"bplist00garbage".hex())

    def test_cli_success_writes_output(self):
        self.build(NUMBERING_MACOS27, values="opack")
        output = os.path.join(self.tmp.name, "out.json")
        run = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--db", self.db, "-o", output],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        with open(output) as fh:
            self.assertEqual(len(json.load(fh)["automations"]), 2)

    def test_unmapped_entity_is_labelled_not_guessed(self):
        numbering = dict(NUMBERING_MACOS27, MKFFutureEvent=150)
        out = self.extract(numbering, lambda c: c.execute(
            "INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER) VALUES (3, 150, 1)"))
        types = [e["eventType"] for e in self.by_name(out, "Automation 1")["events"]]
        self.assertIn("entity:MKFFutureEvent", types)


if __name__ == "__main__":
    unittest.main()
