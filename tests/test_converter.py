#!/usr/bin/env python3
"""Tests for homekit_to_ha.py — domain inference, trigger conversion, action mapping."""

import os
import sys
import unittest

# Add scripts/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from homekit_to_ha import (
    CHAR_MAP,
    SERVICE_DOMAIN_MAP,
    infer_domain_from_service,
    infer_trigger_from_name,
    convert_trigger,
    convert_characteristic_write,
    classify_automation,
    build_audit_report,
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
        """An automation with real trigger, real actions, and no TODOs is 'ready'."""
        auto = {
            "id": "hk_abc123",
            "alias": "[HK] Living Room Lights Off",
            "triggers": [{"platform": "time", "at": "23:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": "light.living_room"}},
            ],
        }
        self.assertEqual(classify_automation(auto), "ready")

    def test_partial_some_todos(self):
        """An automation with real actions + some TODOs is 'partial'."""
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
        result = classify_automation(auto)
        self.assertEqual(result, "partial")

    def test_manual_placeholder_trigger(self):
        """An automation with a placeholder trigger is 'manual'."""
        auto = {
            "id": "hk_ghi789",
            "alias": "[HK] Mystery Automation",
            "triggers": [{"platform": "event", "event_type": "manual_trigger_needed",
                          "_comment": "# TODO: Configure trigger"}],
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.porch"}},
            ],
        }
        self.assertEqual(classify_automation(auto), "manual")

    def test_manual_all_todo_actions(self):
        """An automation where every action is a TODO is 'manual'."""
        auto = {
            "id": "hk_jkl012",
            "alias": "[HK] Shortcut Only",
            "triggers": [{"platform": "time", "at": "08:00:00"}],
            "actions": [
                {"_comment": "# TODO: Unsupported action type: shortcut"},
            ],
        }
        self.assertEqual(classify_automation(auto), "manual")

    def test_ready_multiple_real_actions(self):
        """An automation with multiple real actions and no TODOs is 'ready'."""
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
        self.assertEqual(classify_automation(auto), "ready")


class TestBuildAuditReport(unittest.TestCase):
    """Test that the audit report aggregates correctly."""

    def test_report_structure(self):
        automations = [
            {"alias": "Auto A", "actions": [{"service": "light.turn_on", "target": {"entity_id": "light.a"}}]},
            {"alias": "Auto B", "actions": [{"service": "# TODO: Add actions"}]},
        ]
        classifications = ["ready", "manual"]
        report = build_audit_report(automations, classifications)

        self.assertIn("summary", report)
        self.assertEqual(report["summary"]["ready"], 1)
        self.assertEqual(report["summary"]["manual"], 1)
        self.assertEqual(report["summary"]["partial"], 0)
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
            ["partial", "ready"],
        )
        self.assertGreater(report["summary"]["total_todos"], 0)
        # The clean one should have 0 TODOs
        ready_entry = report["ready"][0]
        self.assertEqual(ready_entry["todos"], 0)

    def test_empty_input(self):
        report = build_audit_report([], [])
        self.assertEqual(report["summary"]["total"], 0)
        self.assertEqual(report["summary"]["ready"], 0)
        self.assertEqual(len(report["ready"]), 0)
        self.assertEqual(len(report["partial"]), 0)
        self.assertEqual(len(report["manual"]), 0)

    def test_all_partial(self):
        autos = [
            {"alias": f"Auto {i}", "actions": [
                {"service": "light.turn_on", "target": {"entity_id": "light.ok"}},
                {"_comment": "# TODO: fix this"},
            ]}
            for i in range(3)
        ]
        report = build_audit_report(autos, ["partial"] * 3)
        self.assertEqual(report["summary"]["partial"], 3)
        self.assertEqual(report["summary"]["ready"], 0)
        self.assertEqual(report["summary"]["manual"], 0)


if __name__ == "__main__":
    unittest.main()
