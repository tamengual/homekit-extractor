#!/usr/bin/env python3
"""Tests for homekit_to_ha.py — domain inference, trigger conversion, action mapping."""

import json
import os
import sys
import unittest

# Add scripts/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from homekit_to_ha import (
    CHAR_MAP,
    SERVICE_DOMAIN_MAP,
    CLASSIFY_READY,
    CLASSIFY_PARTIAL,
    CLASSIFY_MANUAL,
    infer_domain_from_service,
    infer_trigger_from_name,
    convert_trigger,
    convert_characteristic_write,
    classify_automation,
    build_audit_report,
    find_entity,
    convert_automations,
    _summarize_hk_trigger,
    _summarize_hk_actions,
    _summarize_ha_trigger,
    _summarize_ha_actions,
    simulate_output,
    _normalize_automation,
)


class TestInferDomainFromService(unittest.TestCase):
    """Test that Power State and other multi-domain characteristics resolve correctly."""

    def test_lightbulb_maps_to_light(self):
        self.assertEqual(infer_domain_from_service("Lightbulb"), "light")

    def test_fan_maps_to_fan(self):
        self.assertEqual(infer_domain_from_service("Fan"), "fan")

    def test_fanv2_maps_to_fan(self):
        self.assertEqual(infer_domain_from_service("FanV2"), "fan")

    def test_switch_maps_to_switch(self):
        self.assertEqual(infer_domain_from_service("Switch"), "switch")

    def test_outlet_maps_to_switch(self):
        self.assertEqual(infer_domain_from_service("Outlet"), "switch")

    def test_window_covering_maps_to_cover(self):
        self.assertEqual(infer_domain_from_service("Window Covering"), "cover")

    def test_lock_maps_to_lock(self):
        self.assertEqual(infer_domain_from_service("Lock Mechanism"), "lock")

    def test_thermostat_maps_to_climate(self):
        self.assertEqual(infer_domain_from_service("Thermostat"), "climate")

    def test_motion_sensor_maps_to_binary_sensor(self):
        self.assertEqual(infer_domain_from_service("Motion Sensor"), "binary_sensor")

    def test_case_insensitive(self):
        self.assertEqual(infer_domain_from_service("LIGHTBULB"), "light")
        self.assertEqual(infer_domain_from_service("fan"), "fan")

    def test_unknown_falls_back(self):
        self.assertEqual(infer_domain_from_service("UnknownService"), "light")
        self.assertEqual(infer_domain_from_service("UnknownService", "switch"), "switch")

    def test_empty_falls_back(self):
        self.assertEqual(infer_domain_from_service(""), "light")
        self.assertEqual(infer_domain_from_service(None), "light")


class TestCharMapMultiDomain(unittest.TestCase):
    """Verify that Power State is marked as multi_domain and others aren't."""

    def test_power_state_is_multi_domain(self):
        power_state = CHAR_MAP["00000025"]
        self.assertTrue(power_state.get("multi_domain", False))

    def test_brightness_is_not_multi_domain(self):
        brightness = CHAR_MAP["00000008"]
        self.assertFalse(brightness.get("multi_domain", False))

    def test_cover_position_is_not_multi_domain(self):
        position = CHAR_MAP["0000007c"]
        self.assertFalse(position.get("multi_domain", False))


class TestConvertCharacteristicWrite(unittest.TestCase):
    """Test that characteristic writes resolve to the correct HA domain."""

    def _lookup(self, name, room, service):
        """Stub entity lookup that returns a predictable entity_id."""
        slug = name.lower().replace(" ", "_") if name else "unknown"
        return f"light.{slug}", "test"

    def test_power_on_lightbulb(self):
        action = {
            "accessoryName": "Living Room Light",
            "room": "Living Room",
            "serviceName": "Lightbulb",
            "characteristicType": "00000025-0000-1000-8000-0026BB765291",
            "characteristic": "Power State",
            "targetValue": True,
        }
        result = convert_characteristic_write(action, self._lookup)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["service"], "light.turn_on")

    def test_power_on_fan(self):
        action = {
            "accessoryName": "Garage Fan",
            "room": "Garage",
            "serviceName": "Fan",
            "characteristicType": "00000025-0000-1000-8000-0026BB765291",
            "characteristic": "Power State",
            "targetValue": True,
        }
        result = convert_characteristic_write(action, self._lookup)
        self.assertEqual(len(result), 1)
        # Should resolve to fan.turn_on, not light.turn_on
        self.assertEqual(result[0]["service"], "fan.turn_on")

    def test_power_on_switch(self):
        action = {
            "accessoryName": "Patio Outlet",
            "room": "Patio",
            "serviceName": "Outlet",
            "characteristicType": "00000025-0000-1000-8000-0026BB765291",
            "characteristic": "Power State",
            "targetValue": True,
        }
        result = convert_characteristic_write(action, self._lookup)
        self.assertEqual(result[0]["service"], "switch.turn_on")

    def test_power_off_switch(self):
        action = {
            "accessoryName": "Desk Lamp",
            "room": "Office",
            "serviceName": "Switch",
            "characteristicType": "00000025-0000-1000-8000-0026BB765291",
            "characteristic": "Power State",
            "targetValue": False,
        }
        result = convert_characteristic_write(action, self._lookup)
        self.assertEqual(result[0]["service"], "switch.turn_off")

    def test_brightness_stays_light(self):
        action = {
            "accessoryName": "Bedroom Light",
            "room": "Bedroom",
            "serviceName": "Lightbulb",
            "characteristicType": "00000008-0000-1000-8000-0026BB765291",
            "characteristic": "Brightness",
            "targetValue": 80,
        }
        result = convert_characteristic_write(action, self._lookup)
        self.assertEqual(result[0]["service"], "light.turn_on")
        self.assertEqual(result[0]["data"]["brightness_pct"], 80)


class TestInferTriggerFromName(unittest.TestCase):
    def test_time_pattern(self):
        triggers = infer_trigger_from_name("10 00 PM, Daily")
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["platform"], "time")
        self.assertEqual(triggers[0]["at"], "22:00:00")

    def test_button_press(self):
        triggers = infer_trigger_from_name("Bedroom Button Single Press")
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["platform"], "event")
        self.assertIn("single_press", str(triggers[0]))

    def test_sunrise(self):
        triggers = infer_trigger_from_name("Open Blinds at Sunrise")
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["platform"], "sun")
        self.assertEqual(triggers[0]["event"], "sunrise")

    def test_sunset(self):
        triggers = infer_trigger_from_name("Close Blinds at Sunset")
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["platform"], "sun")
        self.assertEqual(triggers[0]["event"], "sunset")

    def test_motion(self):
        triggers = infer_trigger_from_name("Front Door Motion")
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["platform"], "state")

    def test_unknown_gets_todo(self):
        triggers = infer_trigger_from_name("Mysterious Automation")
        self.assertEqual(len(triggers), 1)
        self.assertIn("TODO", str(triggers[0]))


class TestConvertTrigger(unittest.TestCase):
    def test_timer_trigger(self):
        auto = {
            "name": "10 00 PM, Daily",
            "triggerType": "timer",
            "events": [],
        }
        triggers = convert_trigger(auto)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["platform"], "time")
        self.assertEqual(triggers[0]["at"], "22:00:00")

    def test_significant_time_trigger(self):
        auto = {
            "name": "Sunrise Blinds",
            "triggerType": "event",
            "events": [
                {
                    "eventType": "significantTime",
                    "significantEvent": "sunrise",
                    "offsetSeconds": 1800,
                }
            ],
        }
        triggers = convert_trigger(auto)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["platform"], "sun")
        self.assertEqual(triggers[0]["event"], "sunrise")
        self.assertEqual(triggers[0]["offset"], "+00:30:00")

    def test_button_event_trigger(self):
        auto = {
            "name": "Button Press",
            "triggerType": "event",
            "events": [
                {
                    "eventType": "charValue",
                    "characteristic": "Programmable Switch Event",
                    "accessory": "Bedroom Button",
                    "eventValue": 0,
                }
            ],
        }
        triggers = convert_trigger(auto)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]["platform"], "event")


class TestClassifyAutomation(unittest.TestCase):
    """Test that automations are classified correctly based on TODO count and content."""

    def test_ready_no_todos(self):
        """An automation with real trigger, real actions, and no TODOs is READY_TO_TEST."""
        auto = {
            "id": "hk_abc123",
            "alias": "[HK] Living Room Lights Off",
            "triggers": [{"platform": "time", "at": "23:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.living_room"}},
            ],
        }
        cls, reasons = classify_automation(auto)
        self.assertEqual(cls, CLASSIFY_READY)
        self.assertEqual(len(reasons), 0)

    def test_partial_some_todos(self):
        """An automation with real actions + some TODOs is REVIEW_REQUIRED."""
        auto = {
            "id": "hk_def456",
            "alias": "[HK] Bedroom Button Press",
            "triggers": [{"platform": "event", "event_type": "homekit_button_press",
                          "event_data": {"device_name": "Bedroom Button", "action": "single_press"}}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.bedroom"}},
                {"service": "scene.turn_on", "target": {"entity_id": "# TODO: Map scene abc123..."}},
            ],
        }
        cls, reasons = classify_automation(auto)
        self.assertEqual(cls, CLASSIFY_PARTIAL)
        self.assertIn("TODO_ACTION", reasons)

    def test_manual_placeholder_trigger(self):
        """An automation with a placeholder trigger is MANUAL_REBUILD."""
        auto = {
            "id": "hk_ghi789",
            "alias": "[HK] Mystery Automation",
            "triggers": [{"platform": "event", "event_type": "manual_trigger_needed",
                          "_comment": "# TODO: Configure trigger"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.porch"}},
            ],
        }
        cls, reasons = classify_automation(auto)
        self.assertEqual(cls, CLASSIFY_MANUAL)
        self.assertIn("PLACEHOLDER_TRIGGER", reasons)

    def test_manual_all_todo_actions(self):
        """An automation where every action is a TODO is MANUAL_REBUILD."""
        auto = {
            "id": "hk_jkl012",
            "alias": "[HK] Shortcut Only",
            "triggers": [{"platform": "time", "at": "08:00:00"}],
            "actions": [
                {"_comment": "# TODO: Unsupported action type: shortcut"},
            ],
        }
        cls, reasons = classify_automation(auto)
        self.assertEqual(cls, CLASSIFY_MANUAL)
        self.assertIn("ALL_TODO_ACTIONS", reasons)
        self.assertIn("UNSUPPORTED_SHORTCUT_STEP", reasons)

    def test_ready_multiple_real_actions(self):
        """An automation with multiple real actions and no TODOs is READY_TO_TEST."""
        auto = {
            "id": "hk_mno345",
            "alias": "[HK] Goodnight Scene",
            "triggers": [{"platform": "time", "at": "22:30:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.living_room"}},
                {"service": "light.turn_off", "target": {"entity_id": "light.kitchen"}},
                {"service": "cover.close_cover", "target": {"entity_id": "cover.blinds"}},
            ],
        }
        cls, reasons = classify_automation(auto)
        self.assertEqual(cls, CLASSIFY_READY)
        self.assertEqual(len(reasons), 0)

    def test_ambiguous_entity_gets_reason_code(self):
        """An automation with an AMBIGUOUS_ENTITY marker is flagged."""
        auto = {
            "id": "hk_amb001",
            "alias": "[HK] Ambiguous Entity",
            "triggers": [{"platform": "time", "at": "20:00:00"}],
            "actions": [
                {"service": "light.turn_on",
                 "target": {"entity_id": "# TODO: AMBIGUOUS_ENTITY - multiple matches for 'Kitchen Light'"}},
            ],
        }
        cls, reasons = classify_automation(auto)
        self.assertIn("AMBIGUOUS_ENTITY", reasons)


class TestBuildAuditReport(unittest.TestCase):
    """Test that the audit report aggregates correctly."""

    def test_report_structure(self):
        automations = [
            {"alias": "Auto A", "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.a"}}]},
            {"alias": "Auto B", "actions": [{"service": "# TODO: Add actions"}]},
        ]
        classifications = [(CLASSIFY_READY, []), (CLASSIFY_MANUAL, ["ALL_TODO_ACTIONS"])]
        report = build_audit_report(automations, classifications)

        self.assertIn("summary", report)
        self.assertEqual(report["summary"][CLASSIFY_READY], 1)
        self.assertEqual(report["summary"][CLASSIFY_MANUAL], 1)
        self.assertEqual(report["summary"][CLASSIFY_PARTIAL], 0)
        self.assertEqual(report["summary"]["total"], 2)

    def test_todo_counting(self):
        auto_with_todos = {
            "alias": "Has TODOs",
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "# TODO: Map entity"}},
                {"_comment": "# TODO: Unsupported action"},
            ],
        }
        auto_no_todos = {
            "alias": "Clean",
            "actions": [{"service": "light.turn_off", "target": {"entity_id": "light.bedroom"}}],
        }
        report = build_audit_report(
            [auto_with_todos, auto_no_todos],
            [(CLASSIFY_PARTIAL, ["TODO_ACTION"]), (CLASSIFY_READY, [])],
        )
        self.assertGreater(report["summary"]["total_todos"], 0)
        # The clean one should have 0 TODOs
        ready_entry = report[CLASSIFY_READY][0]
        self.assertEqual(ready_entry["todos"], 0)

    def test_empty_input(self):
        report = build_audit_report([], [])
        self.assertEqual(report["summary"]["total"], 0)
        self.assertEqual(report["summary"][CLASSIFY_READY], 0)
        self.assertEqual(len(report[CLASSIFY_READY]), 0)
        self.assertEqual(len(report[CLASSIFY_PARTIAL]), 0)
        self.assertEqual(len(report[CLASSIFY_MANUAL]), 0)

    def test_all_partial(self):
        autos = [
            {"alias": f"Auto {i}", "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.ok"}},
                {"_comment": "# TODO: fix this"},
            ]}
            for i in range(3)
        ]
        report = build_audit_report(autos, [(CLASSIFY_PARTIAL, ["TODO_ACTION"])] * 3)
        self.assertEqual(report["summary"][CLASSIFY_PARTIAL], 3)
        self.assertEqual(report["summary"][CLASSIFY_READY], 0)
        self.assertEqual(report["summary"][CLASSIFY_MANUAL], 0)

    def test_reason_codes_preserved(self):
        """Reason codes from classification should appear in the report entries."""
        auto = {"alias": "Test", "actions": [{"_comment": "# TODO: fix"}]}
        report = build_audit_report(
            [auto],
            [(CLASSIFY_MANUAL, ["ALL_TODO_ACTIONS", "PLACEHOLDER_TRIGGER"])],
        )
        entry = report[CLASSIFY_MANUAL][0]
        self.assertIn("reasons", entry)
        self.assertEqual(len(entry["reasons"]), 2)
        self.assertIn("ALL_TODO_ACTIONS", entry["reasons"])


class TestFindEntityAmbiguity(unittest.TestCase):
    """Test that entity resolution detects and refuses ambiguous matches."""

    def _build_registry(self, names):
        """Build entity lookup structures from a list of (name, entity_id) tuples."""
        import re
        entity_by_name = {}
        entity_by_normalized = {}
        all_entities = []
        for name, eid in names:
            entity_by_name[name] = eid
            normalized = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
            entity_by_normalized[normalized] = eid
            all_entities.append({"entity_id": eid, "name": name, "normalized": normalized})
        return entity_by_name, entity_by_normalized, all_entities

    def test_exact_match_returns_exact(self):
        by_name, by_norm, all_ents = self._build_registry([
            ("Kitchen Light", "light.kitchen"),
            ("Bedroom Light", "light.bedroom"),
        ])
        eid, reason = find_entity("Kitchen Light", "", "", by_name, by_norm, all_ents)
        self.assertEqual(eid, "light.kitchen")
        self.assertEqual(reason, "EXACT_NAME")

    def test_fuzzy_ambiguous_returns_none(self):
        """Two similar entities should trigger AMBIGUOUS_ENTITY."""
        by_name, by_norm, all_ents = self._build_registry([
            ("Kitchen Light 1", "light.kitchen_1"),
            ("Kitchen Light 2", "light.kitchen_2"),
        ])
        eid, reason = find_entity("Kitchen Light", "", "", by_name, by_norm, all_ents)
        # Should be ambiguous (substring match finds both)
        self.assertEqual(reason, "AMBIGUOUS_ENTITY")
        self.assertIsNone(eid)

    def test_clear_fuzzy_match_succeeds(self):
        """A clearly different set should not trigger ambiguity."""
        by_name, by_norm, all_ents = self._build_registry([
            ("Kitchen Light", "light.kitchen"),
            ("Garage Door", "cover.garage_door"),
        ])
        eid, reason = find_entity("Kitchen Lite", "", "", by_name, by_norm, all_ents)
        # Should fuzzy-match to Kitchen Light with high confidence
        self.assertIsNotNone(eid)
        self.assertTrue(reason.startswith("FUZZY_"))

    def test_no_match_returns_unmatched(self):
        by_name, by_norm, all_ents = self._build_registry([
            ("Kitchen Light", "light.kitchen"),
        ])
        eid, reason = find_entity("Patio Heater", "", "", by_name, by_norm, all_ents)
        self.assertIsNone(eid)
        self.assertEqual(reason, "UNMATCHED")

    def test_no_name_returns_no_name(self):
        by_name, by_norm, all_ents = self._build_registry([])
        eid, reason = find_entity("", "", "", by_name, by_norm, all_ents)
        self.assertIsNone(eid)
        self.assertEqual(reason, "NO_NAME")

    def test_room_stripped_match(self):
        by_name, by_norm, all_ents = self._build_registry([
            ("Lamp", "light.lamp"),
        ])
        eid, reason = find_entity("Bedroom Lamp", "Bedroom", "", by_name, by_norm, all_ents)
        self.assertEqual(eid, "light.lamp")
        self.assertEqual(reason, "ROOM_PLUS_SERVICE")


class TestEndToEnd(unittest.TestCase):
    """End-to-end test: merge fixtures -> convert -> verify audit."""

    def test_fixture_to_yaml_pipeline(self):
        """Run the full merge → convert → audit pipeline on test fixtures."""
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")

        # Load fixtures
        with open(os.path.join(fixtures_dir, "app_export_minimal.json"), "r") as f:
            app_data = json.load(f)
        with open(os.path.join(fixtures_dir, "homed_export_minimal.json"), "r") as f:
            homed_data = json.load(f)

        # Merge
        from merge_exports import merge_exports
        merged, merge_stats = merge_exports(app_data, homed_data)

        # Verify merge
        self.assertEqual(merge_stats["matched"], 4)
        self.assertEqual(merge_stats["unmatched_homed"], 1)

        # Convert (with no entity registry — all will be TODOs)
        def stub_lookup(name, room, service):
            return f"# TODO: Map entity for '{name}'", "NO_MAP"

        automations, audit = convert_automations(merged, stub_lookup)

        # Should have 5 automations (4 matched + 1 homed-only)
        self.assertEqual(len(automations), 5)

        # Audit summary should be populated
        s = audit["summary"]
        self.assertEqual(s["total"], 5)
        self.assertGreater(s["total_todos"], 0)

        # Without entity mapping, nothing should be READY_TO_TEST
        self.assertEqual(s[CLASSIFY_READY], 0)

        # All automations should have an id and alias
        for auto in automations:
            self.assertIn("id", auto)
            self.assertIn("alias", auto)
            self.assertTrue(auto["alias"].startswith("[HK]"))

        # Verify audit has all three classification lists
        self.assertIn(CLASSIFY_READY, audit)
        self.assertIn(CLASSIFY_PARTIAL, audit)
        self.assertIn(CLASSIFY_MANUAL, audit)

        # Every automation should appear in exactly one classification bucket
        all_names_in_audit = set()
        for bucket in [CLASSIFY_READY, CLASSIFY_PARTIAL, CLASSIFY_MANUAL]:
            for entry in audit[bucket]:
                all_names_in_audit.add(entry["name"])
        self.assertEqual(len(all_names_in_audit), 5)


class TestNormalizeAutomation(unittest.TestCase):
    """Test that app export key names get normalized to canonical format."""

    def test_type_to_actionType(self):
        """App export 'type' on actions should become 'actionType'."""
        auto = {
            "name": "Test",
            "actionSets": [{"actions": [
                {"type": "characteristicWrite", "accessoryName": "Light", "roomName": "Living Room"},
            ]}],
        }
        result = _normalize_automation(auto)
        action = result["actionSets"][0]["actions"][0]
        self.assertEqual(action["actionType"], "characteristicWrite")
        self.assertEqual(action["room"], "Living Room")

    def test_nonCharacteristic_becomes_shortcut(self):
        """App export 'nonCharacteristic' type should become 'shortcut' actionType."""
        auto = {
            "name": "Test",
            "actionSets": [{"actions": [
                {"type": "nonCharacteristic", "description": "Shortcut action"},
            ]}],
        }
        result = _normalize_automation(auto)
        self.assertEqual(result["actionSets"][0]["actions"][0]["actionType"], "shortcut")

    def test_trigger_events_promoted(self):
        """App export nested trigger.events should be promoted to top-level events."""
        auto = {
            "name": "Test",
            "trigger": {
                "events": [{"eventType": "charValue", "characteristic": "Motion"}],
                "executeOnce": False,
            },
            "actionSets": [{"actions": []}],
        }
        result = _normalize_automation(auto)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["eventType"], "charValue")

    def test_homed_format_unchanged(self):
        """Automations already in homed format should pass through unchanged."""
        auto = {
            "name": "Test",
            "triggerType": "event",
            "events": [{"eventType": "charValue"}],
            "actionSets": [{"actions": [
                {"actionType": "characteristicWrite", "room": "Office"},
            ]}],
        }
        result = _normalize_automation(auto)
        action = result["actionSets"][0]["actions"][0]
        self.assertEqual(action["actionType"], "characteristicWrite")
        self.assertEqual(action["room"], "Office")

    def test_missing_events_gets_empty_list(self):
        """Automations without events key should get empty list."""
        auto = {"name": "Test", "actionSets": [{"actions": []}]}
        result = _normalize_automation(auto)
        self.assertEqual(result["events"], [])


class TestSimulateHelpers(unittest.TestCase):
    """Test the simulate/preview helper functions."""

    def test_summarize_hk_trigger_timer(self):
        auto = {"name": "10 00 PM, Daily", "triggerType": "timer", "events": []}
        result = _summarize_hk_trigger(auto)
        self.assertIn("Timer", result)
        self.assertIn("10 00 PM", result)

    def test_summarize_hk_trigger_significant_time(self):
        auto = {
            "name": "Sunrise",
            "triggerType": "event",
            "events": [{"eventType": "significantTime", "significantEvent": "sunrise", "offsetSeconds": 1800}],
        }
        result = _summarize_hk_trigger(auto)
        self.assertIn("sunrise", result)
        self.assertIn("+1800s", result)

    def test_summarize_hk_trigger_char_value(self):
        auto = {
            "name": "Motion",
            "triggerType": "event",
            "events": [{"eventType": "charValue", "characteristic": "Motion Detected",
                         "accessory": "Front Door Camera", "eventValue": True}],
        }
        result = _summarize_hk_trigger(auto)
        self.assertIn("Front Door Camera", result)
        self.assertIn("Motion Detected", result)

    def test_summarize_hk_actions_characteristic_write(self):
        auto = {
            "actionSets": [{
                "actions": [{
                    "actionType": "characteristicWrite",
                    "accessoryName": "Living Room Light",
                    "serviceName": "Lightbulb",
                    "characteristic": "Power State",
                    "targetValue": False,
                }]
            }]
        }
        lines = _summarize_hk_actions(auto)
        self.assertEqual(len(lines), 1)
        self.assertIn("Living Room Light", lines[0])
        self.assertIn("Power State", lines[0])

    def test_summarize_hk_actions_shortcut_opaque(self):
        auto = {
            "actionSets": [{
                "actions": [{"actionType": "shortcut"}]
            }]
        }
        lines = _summarize_hk_actions(auto)
        self.assertEqual(len(lines), 1)
        self.assertIn("Shortcut", lines[0])

    def test_summarize_hk_actions_empty(self):
        auto = {"actionSets": [{"actions": []}]}
        lines = _summarize_hk_actions(auto)
        self.assertEqual(lines, ["(no actions)"])

    def test_summarize_ha_trigger_time(self):
        ha_auto = {"triggers": [{"platform": "time", "at": "22:00:00"}]}
        result = _summarize_ha_trigger(ha_auto)
        self.assertIn("time", result)
        self.assertIn("22:00:00", result)

    def test_summarize_ha_trigger_sun(self):
        ha_auto = {"triggers": [{"platform": "sun", "event": "sunrise"}]}
        result = _summarize_ha_trigger(ha_auto)
        self.assertIn("sun", result)
        self.assertIn("sunrise", result)

    def test_summarize_ha_trigger_placeholder(self):
        ha_auto = {"triggers": [{"platform": "event", "event_type": "manual_trigger_needed"}]}
        result = _summarize_ha_trigger(ha_auto)
        self.assertIn("PLACEHOLDER", result)

    def test_summarize_ha_trigger_empty(self):
        ha_auto = {"triggers": []}
        result = _summarize_ha_trigger(ha_auto)
        self.assertEqual(result, "(no trigger)")

    def test_summarize_ha_actions_service(self):
        ha_auto = {
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.kitchen"}, "data": {}},
            ]
        }
        lines = _summarize_ha_actions(ha_auto)
        self.assertEqual(len(lines), 1)
        self.assertIn("light.turn_on", lines[0])
        self.assertIn("light.kitchen", lines[0])

    def test_summarize_ha_actions_todo(self):
        ha_auto = {
            "actions": [
                {"_comment": "# TODO: Unsupported action type: shortcut"},
            ]
        }
        lines = _summarize_ha_actions(ha_auto)
        self.assertEqual(len(lines), 1)
        self.assertIn("TODO", lines[0])

    def test_summarize_ha_actions_choose(self):
        ha_auto = {
            "actions": [
                {"choose": [{"conditions": [], "sequence": []}], "default": []},
            ]
        }
        lines = _summarize_ha_actions(ha_auto)
        self.assertEqual(len(lines), 1)
        self.assertIn("choose", lines[0])
        self.assertIn("default", lines[0])

    def test_summarize_ha_actions_empty(self):
        ha_auto = {"actions": []}
        lines = _summarize_ha_actions(ha_auto)
        self.assertEqual(lines, ["  (no actions)"])


class TestSimulateOutput(unittest.TestCase):
    """Test the full simulate_output function."""

    def test_simulate_output_produces_output(self):
        """simulate_output should write a formatted report to the given file."""
        import io
        audit = {
            "summary": {
                "total": 2,
                CLASSIFY_READY: 1,
                CLASSIFY_PARTIAL: 1,
                CLASSIFY_MANUAL: 0,
            },
            "_source_pairs": [
                (
                    # HomeKit source
                    {"name": "Living Room Off", "enabled": True, "triggerType": "timer",
                     "events": [],
                     "actionSets": [{"actions": [{"actionType": "characteristicWrite",
                      "accessoryName": "Living Room Light", "serviceName": "Lightbulb",
                      "characteristic": "Power State", "targetValue": False}]}]},
                    # HA converted
                    {"triggers": [{"platform": "time", "at": "23:00:00"}],
                     "actions": [{"service": "light.turn_off",
                      "target": {"entity_id": "light.living_room"}}]},
                    CLASSIFY_READY, [],
                ),
                (
                    # HomeKit source (shortcut)
                    {"name": "Button Shortcut", "enabled": True, "triggerType": "event",
                     "events": [{"eventType": "charValue", "characteristic": "Programmable Switch Event",
                      "accessory": "Bedroom Button", "eventValue": 0}],
                     "actionSets": [{"actions": [{"actionType": "shortcut"}]}]},
                    # HA converted (with TODO)
                    {"triggers": [{"platform": "event", "event_type": "homekit_button_press",
                     "event_data": {"device_name": "Bedroom Button", "action": "single_press"}}],
                     "actions": [{"_comment": "# TODO: Unsupported action type: shortcut"}]},
                    CLASSIFY_PARTIAL, ["TODO_ACTION"],
                ),
            ],
        }

        buf = io.StringIO()
        simulate_output(audit, file=buf)
        output = buf.getvalue()

        # Should contain both automation names
        self.assertIn("Living Room Off", output)
        self.assertIn("Button Shortcut", output)

        # Should contain classification labels
        self.assertIn("READY_TO_TEST", output)
        self.assertIn("REVIEW_REQUIRED", output)

        # Should contain source and converted sections
        self.assertIn("HOMEKIT SOURCE", output)
        self.assertIn("HOME ASSISTANT CONVERSION", output)

        # Should contain risk assessment
        self.assertIn("RISK", output)

        # Should contain simulation summary
        self.assertIn("SIMULATION COMPLETE", output)
        self.assertIn("2 automations reviewed", output)

    def test_simulate_empty_pairs(self):
        """simulate_output with no source pairs should print a message."""
        import io
        audit = {"_source_pairs": []}
        buf = io.StringIO()
        simulate_output(audit, file=buf)
        self.assertIn("No automations to simulate", buf.getvalue())

    def test_simulate_disabled_automation(self):
        """Disabled automations should show Enabled: No."""
        import io
        audit = {
            "summary": {"total": 1, CLASSIFY_READY: 1, CLASSIFY_PARTIAL: 0, CLASSIFY_MANUAL: 0},
            "_source_pairs": [
                (
                    {"name": "Disabled Auto", "enabled": False, "triggerType": "timer",
                     "events": [],
                     "actionSets": [{"actions": [{"actionType": "characteristicWrite",
                      "accessoryName": "Light", "serviceName": "Lightbulb",
                      "characteristic": "Power State", "targetValue": True}]}]},
                    {"triggers": [{"platform": "time", "at": "08:00:00"}],
                     "actions": [{"service": "light.turn_on",
                      "target": {"entity_id": "light.bedroom"}}]},
                    CLASSIFY_READY, [],
                ),
            ],
        }
        buf = io.StringIO()
        simulate_output(audit, file=buf)
        self.assertIn("Enabled: No", buf.getvalue())

    def test_simulate_reasons_shown(self):
        """Reason codes should be displayed in the simulation output."""
        import io
        audit = {
            "summary": {"total": 1, CLASSIFY_READY: 0, CLASSIFY_PARTIAL: 0, CLASSIFY_MANUAL: 1},
            "_source_pairs": [
                (
                    {"name": "Bad Auto", "enabled": True, "triggerType": "timer",
                     "events": [],
                     "actionSets": [{"actions": [{"actionType": "shortcut"}]}]},
                    {"triggers": [{"platform": "event", "event_type": "manual_trigger_needed",
                     "_comment": "# TODO: Configure trigger"}],
                     "actions": [{"_comment": "# TODO: Unsupported action type: shortcut"}]},
                    CLASSIFY_MANUAL, ["PLACEHOLDER_TRIGGER", "ALL_TODO_ACTIONS"],
                ),
            ],
        }
        buf = io.StringIO()
        simulate_output(audit, file=buf)
        output = buf.getvalue()
        self.assertIn("PLACEHOLDER_TRIGGER", output)
        self.assertIn("ALL_TODO_ACTIONS", output)
        self.assertIn("HIGH", output)  # Risk should be HIGH


if __name__ == "__main__":
    unittest.main()
