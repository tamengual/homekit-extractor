#!/usr/bin/env python3
"""
homekit_to_ha.py — Convert HomeKit automations to Home Assistant YAML.

Takes a merged HomeKit export (from merge_exports.py) and converts all automations
to Home Assistant automation YAML format, including:
- Trigger mapping (time, sun, button press, state change, etc.)
- Characteristic-to-service-call translation (brightness, color, temperature, etc.)
- Conditional logic preservation (toggle patterns, if/else branches)
- Entity mapping from HomeKit accessory names to HA entity IDs

Usage:
    python3 homekit_to_ha.py \
        --export homekit_merged.json \
        --entity-registry core.entity_registry \
        -o homekit_automations.yaml

    python3 homekit_to_ha.py \
        --export homekit_merged.json \
        --entity-map entity_map.json \
        -o homekit_automations.yaml

Requirements:
    Python 3.9+ (stdlib only — no external packages)
"""

import json
import argparse
import hashlib
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher

# ============ HOMEKIT CHARACTERISTIC UUIDS ============

# Standard HomeKit characteristic type suffixes (last 8 hex digits of UUID)
# Full UUID format: 0000XXXX-0000-1000-8000-0026BB765291
CHAR_MAP = {
    "00000025": {"name": "Power State", "domain": "light", "param": None},
    "00000008": {"name": "Brightness", "domain": "light", "param": "brightness_pct"},
    "00000013": {"name": "Hue", "domain": "light", "param": "hs_color_h"},
    "0000002f": {"name": "Saturation", "domain": "light", "param": "hs_color_s"},
    "000000ce": {"name": "Color Temperature", "domain": "light", "param": "color_temp"},
    "0000007c": {"name": "Target Position", "domain": "cover", "param": "position"},
    "0000001e": {"name": "Lock Target State", "domain": "lock", "param": None},
    "000000b0": {"name": "Active", "domain": "fan", "param": None},
    "00000029": {"name": "Rotation Speed", "domain": "fan", "param": "percentage"},
    "00000035": {"name": "Target Temperature", "domain": "climate", "param": "temperature"},
    "00000073": {"name": "Programmable Switch Event", "domain": "button", "param": None},
    "000000f1": {"name": "Target Heating Cooling State", "domain": "climate", "param": None},
}

# Programmable Switch Event values
SWITCH_EVENT_MAP = {0: "single_press", 1: "double_press", 2: "long_press"}


# ============ ENTITY MAPPING ============

def build_entity_map_from_registry(registry_path):
    """Build a HomeKit accessory name → HA entity_id mapping from the HA entity registry.

    Uses multiple matching strategies:
    1. Exact name match
    2. Normalized name match (lowercase, strip special chars)
    3. Substring match
    4. Fuzzy match (SequenceMatcher > 0.6)
    """
    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    entities = registry.get("data", {}).get("entities", [])

    # Build lookup structures
    entity_by_name = {}  # original_name -> entity_id
    entity_by_normalized = {}  # normalized_name -> entity_id
    all_entities = []

    for ent in entities:
        eid = ent.get("entity_id", "")
        name = ent.get("name") or ent.get("original_name", "")
        if not eid or not name:
            continue

        entity_by_name[name] = eid
        normalized = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
        entity_by_normalized[normalized] = eid
        all_entities.append({"entity_id": eid, "name": name, "normalized": normalized})

    return entity_by_name, entity_by_normalized, all_entities


def find_entity(accessory_name, room_name, service_name, entity_by_name, entity_by_normalized, all_entities):
    """Find the best matching HA entity_id for a HomeKit accessory.

    Tries multiple strategies in order of confidence.
    """
    if not accessory_name:
        return None, "no_name"

    # Strategy 1: Exact name match
    if accessory_name in entity_by_name:
        return entity_by_name[accessory_name], "exact"

    # Strategy 2: Normalized match
    normalized = re.sub(r"[^a-z0-9 ]", "", accessory_name.lower()).strip()
    if normalized in entity_by_normalized:
        return entity_by_normalized[normalized], "normalized"

    # Strategy 3: Try with service name
    if service_name and service_name != accessory_name:
        if service_name in entity_by_name:
            return entity_by_name[service_name], "service_name"
        svc_norm = re.sub(r"[^a-z0-9 ]", "", service_name.lower()).strip()
        if svc_norm in entity_by_normalized:
            return entity_by_normalized[svc_norm], "service_normalized"

    # Strategy 4: Substring match
    for ent in all_entities:
        if normalized in ent["normalized"] or ent["normalized"] in normalized:
            return ent["entity_id"], "substring"

    # Strategy 5: Fuzzy match
    best_score = 0
    best_match = None
    for ent in all_entities:
        score = SequenceMatcher(None, normalized, ent["normalized"]).ratio()
        if score > best_score:
            best_score = score
            best_match = ent["entity_id"]

    if best_score > 0.6:
        return best_match, f"fuzzy_{best_score:.2f}"

    # Strategy 6: Try stripping room prefix
    if room_name:
        stripped = accessory_name.replace(room_name, "").strip()
        if stripped:
            stripped_norm = re.sub(r"[^a-z0-9 ]", "", stripped.lower()).strip()
            if stripped_norm in entity_by_normalized:
                return entity_by_normalized[stripped_norm], "room_stripped"

    return None, "unmatched"


# ============ TRIGGER CONVERSION ============

def convert_trigger(automation):
    """Convert HomeKit trigger data to HA trigger format."""
    name = automation.get("name", "")
    trigger_type = automation.get("triggerType", "")
    events = automation.get("events", [])

    triggers = []

    # Timer triggers
    if trigger_type == "timer":
        fire_date = automation.get("fireDate")
        recurrence = automation.get("recurrence")

        # Try to parse time from name (e.g., "10 00 PM, Daily")
        time_match = re.search(r"(\d{1,2})\s*(\d{2})\s*(AM|PM)", name, re.IGNORECASE)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            ampm = time_match.group(3).upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0
            triggers.append({
                "platform": "time",
                "at": f"{hour:02d}:{minute:02d}:00",
            })
        elif fire_date:
            triggers.append({
                "platform": "time",
                "at": fire_date,
                "_comment": "# TODO: Verify time format",
            })

    # Event triggers
    for event in events:
        evt_type = event.get("eventType", "")

        if evt_type == "significantTime":
            sig = event.get("significantEvent", "")
            offset = event.get("offsetSeconds", 0)
            trigger = {
                "platform": "sun",
                "event": sig if sig in ("sunrise", "sunset") else "sunrise",
            }
            if offset:
                sign = "+" if offset >= 0 else "-"
                abs_offset = abs(int(offset))
                hours = abs_offset // 3600
                minutes = (abs_offset % 3600) // 60
                seconds = abs_offset % 60
                trigger["offset"] = f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
            triggers.append(trigger)

        elif evt_type == "charValue":
            char_name = event.get("characteristic", "")
            accessory = event.get("accessory", "")
            value = event.get("eventValue")

            if "Programmable Switch" in char_name or "switch" in char_name.lower():
                # Button press trigger
                action = SWITCH_EVENT_MAP.get(value, f"event_{value}") if isinstance(value, int) else str(value)
                triggers.append({
                    "platform": "event",
                    "event_type": "homekit_button_press",
                    "event_data": {
                        "device_name": accessory,
                        "action": action,
                    },
                    "_comment": f"# TODO: Replace with actual device trigger for '{accessory}'",
                })
            elif "motion" in char_name.lower() or "occupancy" in char_name.lower():
                triggers.append({
                    "platform": "state",
                    "entity_id": f"binary_sensor.{re.sub(r'[^a-z0-9]', '_', accessory.lower())}",
                    "to": "on" if value else "off",
                    "_comment": f"# TODO: Verify entity_id for '{accessory}'",
                })
            else:
                triggers.append({
                    "platform": "state",
                    "entity_id": f"sensor.{re.sub(r'[^a-z0-9]', '_', accessory.lower())}",
                    "_comment": f"# TODO: Configure trigger for '{accessory}' {char_name}={value}",
                })

        elif evt_type == "presence":
            triggers.append({
                "platform": "state",
                "entity_id": "person.home",
                "_comment": "# TODO: Configure presence trigger",
            })

        elif evt_type == "location":
            triggers.append({
                "platform": "zone",
                "entity_id": "person.home",
                "zone": "zone.home",
                "event": "enter",
                "_comment": "# TODO: Configure location trigger",
            })

    # Fallback: try to infer trigger from automation name
    if not triggers:
        triggers = infer_trigger_from_name(name)

    return triggers


def infer_trigger_from_name(name):
    """Infer trigger from automation name when no explicit trigger data is available."""
    triggers = []
    name_lower = name.lower()

    # Time patterns
    time_match = re.search(r"(\d{1,2})\s*(\d{2})\s*(am|pm)", name_lower)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        if time_match.group(3) == "pm" and hour != 12:
            hour += 12
        elif time_match.group(3) == "am" and hour == 12:
            hour = 0
        return [{"platform": "time", "at": f"{hour:02d}:{minute:02d}:00"}]

    # Button press patterns
    press_match = re.search(r"(.+?)\s+(single|double|long)\s+press", name_lower)
    if press_match:
        device = press_match.group(1).strip()
        # Remove trailing number suffixes like "2", "3" etc.
        device = re.sub(r"\s+\d+$", "", device).strip()
        action = press_match.group(2) + "_press"
        return [{
            "platform": "event",
            "event_type": "homekit_button_press",
            "event_data": {"device_name": device.title(), "action": action},
            "_comment": f"# TODO: Replace with actual device trigger",
        }]

    # Motion/occupancy patterns
    if "motion" in name_lower or "occupancy" in name_lower:
        return [{
            "platform": "state",
            "entity_id": "binary_sensor.todo_motion_sensor",
            "to": "on",
            "_comment": f"# TODO: Configure motion/occupancy trigger for '{name}'",
        }]

    # Door/window sensor patterns
    if "door" in name_lower and ("open" in name_lower or "close" in name_lower):
        state = "on" if "open" in name_lower else "off"
        return [{
            "platform": "state",
            "entity_id": "binary_sensor.todo_door_sensor",
            "to": state,
            "_comment": f"# TODO: Configure door sensor trigger for '{name}'",
        }]

    # Sunrise/sunset
    if "sunrise" in name_lower or "sunset" in name_lower:
        event = "sunrise" if "sunrise" in name_lower else "sunset"
        return [{"platform": "sun", "event": event}]

    # Default
    return [{
        "platform": "event",
        "event_type": "manual_trigger_needed",
        "_comment": f"# TODO: Configure trigger for '{name}'",
    }]


# ============ ACTION CONVERSION ============

def convert_action(action, entity_lookup):
    """Convert a single HomeKit action to HA service call(s)."""
    action_type = action.get("actionType", "")

    if action_type == "characteristicWrite":
        return convert_characteristic_write(action, entity_lookup)
    elif action_type == "shortcut":
        return convert_shortcut_action(action, entity_lookup)
    else:
        return [{"_comment": f"# TODO: Unsupported action type: {action_type}"}]


def convert_characteristic_write(action, entity_lookup):
    """Convert a HomeKit characteristic write to HA service call."""
    accessory = action.get("accessoryName", "")
    room = action.get("room", "")
    service = action.get("serviceName", "")
    char_type = action.get("characteristicType", "")
    char_desc = action.get("characteristic", "")
    target = action.get("targetValue")

    # Find HA entity
    entity_id, match_type = entity_lookup(accessory, room, service)

    # Determine service call based on characteristic
    char_suffix = char_type[-8:].lower() if char_type and len(char_type) >= 8 else ""
    char_info = CHAR_MAP.get(char_suffix, {})

    domain = char_info.get("domain", "light")
    param = char_info.get("param")

    # Build service call
    if char_suffix == "00000025" or "power" in char_desc.lower():
        # Power state
        on_off = "on" if target else "off"
        svc = {"service": f"{domain}.turn_{on_off}"}
        if entity_id:
            svc["target"] = {"entity_id": entity_id}
        else:
            svc["target"] = {"entity_id": f"# TODO: Map entity for '{accessory}'"}
        return [svc]

    elif char_suffix == "0000001e" or "lock" in char_desc.lower():
        # Lock
        action_name = "lock" if target else "unlock"
        svc = {"service": f"lock.{action_name}"}
        if entity_id:
            svc["target"] = {"entity_id": entity_id}
        else:
            svc["target"] = {"entity_id": f"# TODO: Map entity for '{accessory}'"}
        return [svc]

    elif param:
        # Parameterized service call (brightness, color, etc.)
        svc = {
            "service": f"{domain}.turn_on",
            "data": {},
        }
        if entity_id:
            svc["target"] = {"entity_id": entity_id}
        else:
            svc["target"] = {"entity_id": f"# TODO: Map entity for '{accessory}'"}

        if param == "brightness_pct":
            svc["data"]["brightness_pct"] = target
        elif param == "hs_color_h":
            svc["data"]["hs_color"] = [target, 100]  # Will be merged with saturation
        elif param == "hs_color_s":
            svc["data"]["hs_color"] = [0, target]  # Will be merged with hue
        elif param == "color_temp":
            svc["data"]["color_temp"] = target
        elif param == "position":
            svc = {
                "service": "cover.set_cover_position",
                "data": {"position": target},
            }
            if entity_id:
                svc["target"] = {"entity_id": entity_id}
            else:
                svc["target"] = {"entity_id": f"# TODO: Map entity for '{accessory}'"}
        elif param == "percentage":
            svc = {
                "service": f"{domain}.set_percentage",
                "data": {"percentage": target},
            }
            if entity_id:
                svc["target"] = {"entity_id": entity_id}
            else:
                svc["target"] = {"entity_id": f"# TODO: Map entity for '{accessory}'"}
        else:
            svc["data"][param] = target

        return [svc]

    else:
        # Unknown characteristic
        svc = {
            "service": f"{domain}.turn_on",
            "_comment": f"# TODO: Unknown characteristic '{char_desc}' = {target} for '{accessory}'",
        }
        if entity_id:
            svc["target"] = {"entity_id": entity_id}
        return [svc]


def convert_shortcut_action(action, entity_lookup):
    """Convert a shortcut workflow to HA choose/sequence actions."""
    steps = action.get("workflowSteps", [])
    if not steps:
        return [{"_comment": "# TODO: Empty shortcut action"}]

    return build_ha_actions_from_workflow(steps, entity_lookup)


def build_ha_actions_from_workflow(steps, entity_lookup):
    """Recursively convert workflow steps to HA actions."""
    actions = []
    i = 0

    while i < len(steps):
        step = steps[i]
        step_type = step.get("type", "")

        if step_type == "conditional" and step.get("mode") == "if":
            # Build a choose block
            choose_block, end_idx = build_choose_block(steps, i, entity_lookup)
            actions.append(choose_block)
            i = end_idx + 1

        elif step_type == "homeaccessory":
            # Direct device action
            for aset in step.get("actionSets", []):
                scene_id = aset.get("sceneModelID", "")
                for wa in aset.get("writeActions", []):
                    if wa.get("targetValue") is not None:
                        # Has actual value — would need entity mapping
                        actions.append({
                            "_comment": f"# Device action: target={wa['targetValue']}",
                        })
                if not aset.get("writeActions") and scene_id:
                    actions.append({
                        "service": "scene.turn_on",
                        "target": {"entity_id": f"# TODO: Map scene {scene_id[:12]}..."},
                    })
            i += 1

        elif step_type == "choosefrommenu":
            actions.append({
                "_comment": f"# TODO: Menu action (mode={step.get('mode')})",
            })
            i += 1

        else:
            i += 1

    return actions


def build_choose_block(steps, start_idx, entity_lookup):
    """Build an HA choose block from workflow conditional steps."""
    # Find matching else and end
    depth = 0
    if_actions = []
    else_actions = []
    in_else = False
    end_idx = start_idx

    condition_step = steps[start_idx]
    condition_type = condition_step.get("condition", "")
    input_type = condition_step.get("inputType", "")

    for i in range(start_idx + 1, len(steps)):
        step = steps[i]
        if step.get("type") == "conditional":
            if step.get("mode") == "if":
                depth += 1
            elif step.get("mode") == "end":
                if depth == 0:
                    end_idx = i
                    break
                depth -= 1
            elif step.get("mode") == "else" and depth == 0:
                in_else = True
                continue

        if depth == 0:
            if in_else:
                else_actions.append(step)
            else:
                if_actions.append(step)
        else:
            if in_else:
                else_actions.append(step)
            else:
                if_actions.append(step)

    # Build the choose block
    if_sequence = build_ha_actions_from_workflow(if_actions, entity_lookup)
    else_sequence = build_ha_actions_from_workflow(else_actions, entity_lookup)

    # Build condition
    if input_type == "HomeAccessory" and condition_type in ("is", "equals"):
        condition = {
            "condition": "state",
            "entity_id": "# TODO: Set entity to check state of",
            "state": "on",
        }
    else:
        condition = {
            "condition": "template",
            "value_template": f"# TODO: Determine condition (HK: '{condition_type}', input: '{input_type}')",
        }

    choose = {"choose": []}
    if if_sequence:
        choose["choose"].append({
            "conditions": [condition],
            "sequence": if_sequence,
        })
    if else_sequence:
        choose["default"] = else_sequence

    return choose, end_idx


# ============ MAIN CONVERSION ============

def convert_automations(export_data, entity_lookup):
    """Convert all HomeKit automations to HA YAML format."""
    automations = []

    for auto in export_data.get("automations", []):
        name = auto.get("name", "Unknown")
        enabled = auto.get("enabled", True)

        # Generate unique ID
        auto_id = "hk_" + hashlib.md5(name.encode()).hexdigest()[:12]

        # Convert triggers
        triggers = convert_trigger(auto)

        # Convert actions
        ha_actions = []
        for aset in auto.get("actionSets", []):
            for action in aset.get("actions", []):
                ha_actions.extend(convert_action(action, entity_lookup))

        # Build HA automation
        ha_auto = {
            "id": auto_id,
            "alias": f"[HK] {name}",
            "description": f"Converted from HomeKit automation '{name}'",
            "mode": "single",
            "triggers": triggers,
            "conditions": [],
            "actions": ha_actions if ha_actions else [{"service": "# TODO: Add actions"}],
        }

        automations.append(ha_auto)

    return automations


def to_yaml(automations):
    """Convert automation list to YAML string (simple formatter, no PyYAML needed)."""
    lines = []

    for auto in automations:
        lines.append(f"- id: {auto['id']}")
        lines.append(f"  alias: '{auto['alias']}'")
        lines.append(f"  description: '{auto['description']}'")
        lines.append(f"  mode: {auto['mode']}")

        # Triggers
        lines.append("  triggers:")
        for trigger in auto.get("triggers", []):
            comment = trigger.pop("_comment", None)
            if comment:
                lines.append(f"  {comment}")
            lines.append(f"  - platform: {trigger.get('platform', 'event')}")
            for k, v in trigger.items():
                if k == "platform":
                    continue
                if isinstance(v, dict):
                    lines.append(f"    {k}:")
                    for dk, dv in v.items():
                        lines.append(f"      {dk}: {format_yaml_value(dv)}")
                else:
                    lines.append(f"    {k}: {format_yaml_value(v)}")

        # Conditions
        lines.append("  conditions: []")

        # Actions
        lines.append("  actions:")
        render_actions(auto.get("actions", []), lines, indent=2)

        lines.append("")

    return "\n".join(lines)


def render_actions(actions, lines, indent=2):
    """Recursively render actions to YAML lines."""
    prefix = "  " * indent

    for action in actions:
        if "_comment" in action and len(action) == 1:
            lines.append(f"{prefix}{action['_comment']}")
            continue

        if "choose" in action:
            lines.append(f"{prefix}- choose:")
            for choice in action["choose"]:
                lines.append(f"{prefix}  - conditions:")
                for cond in choice.get("conditions", []):
                    lines.append(f"{prefix}    - condition: {cond.get('condition', 'state')}")
                    for k, v in cond.items():
                        if k == "condition":
                            continue
                        lines.append(f"{prefix}      {k}: {format_yaml_value(v)}")
                lines.append(f"{prefix}    sequence:")
                render_actions(choice.get("sequence", []), lines, indent + 2)
            if "default" in action:
                lines.append(f"{prefix}  default:")
                render_actions(action["default"], lines, indent + 1)
            continue

        comment = action.pop("_comment", None) if isinstance(action, dict) else None
        if comment:
            lines.append(f"{prefix}{comment}")

        if "service" in action:
            lines.append(f"{prefix}- service: {action['service']}")
            if "target" in action:
                lines.append(f"{prefix}  target:")
                for k, v in action["target"].items():
                    lines.append(f"{prefix}    {k}: {format_yaml_value(v)}")
            if "data" in action:
                lines.append(f"{prefix}  data:")
                for k, v in action["data"].items():
                    lines.append(f"{prefix}    {k}: {format_yaml_value(v)}")


def format_yaml_value(value):
    """Format a value for YAML output."""
    if isinstance(value, bool):
        return "'on'" if value else "'off'"
    if isinstance(value, str):
        if value.startswith("#"):
            return f"'{value}'"
        return f"'{value}'" if any(c in value for c in ":#{}[]|>&*!%@") else value
    if isinstance(value, (list, tuple)):
        return f"[{', '.join(str(v) for v in value)}]"
    return str(value)


def main():
    parser = argparse.ArgumentParser(
        description="Convert HomeKit automations to Home Assistant YAML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--export",
        required=True,
        help="Path to HomeKit export JSON (merged or standalone)",
    )
    parser.add_argument(
        "--entity-registry",
        help="Path to HA core.entity_registry file (for automatic entity mapping)",
    )
    parser.add_argument(
        "--entity-map",
        help="Path to manual entity map JSON (accessory_name -> entity_id)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output YAML file path (default: stdout)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed conversion statistics",
    )
    args = parser.parse_args()

    # Load export
    with open(args.export, "r", encoding="utf-8") as f:
        export_data = json.load(f)

    # Build entity lookup
    if args.entity_registry:
        by_name, by_norm, all_ents = build_entity_map_from_registry(args.entity_registry)
        def entity_lookup(name, room, service):
            return find_entity(name, room, service, by_name, by_norm, all_ents)
    elif args.entity_map:
        with open(args.entity_map, "r") as f:
            manual_map = json.load(f)
        def entity_lookup(name, room, service):
            return manual_map.get(name, (None, "no_map"))
    else:
        print("Warning: No entity mapping provided. Entity IDs will be TODOs.", file=sys.stderr)
        def entity_lookup(name, room, service):
            entity_id = f"# TODO: Map entity for '{name}'"
            return entity_id, "no_map"

    # Convert
    automations = convert_automations(export_data, entity_lookup)

    # Output
    yaml_output = to_yaml(automations)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(yaml_output)
        print(f"Written {len(automations)} automations to {args.output}", file=sys.stderr)
    else:
        print(yaml_output)

    # Stats
    total_actions = sum(len(a.get("actions", [])) for a in automations)
    print(f"\n--- Conversion Statistics ---", file=sys.stderr)
    print(f"Automations converted: {len(automations)}", file=sys.stderr)
    print(f"Total action blocks:   {total_actions}", file=sys.stderr)


if __name__ == "__main__":
    main()
