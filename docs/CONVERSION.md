# HomeKit to Home Assistant Conversion Guide

A practical, step-by-step reference for converting Apple HomeKit automations to Home Assistant YAML. Covers both automated conversion (via `homekit_to_ha.py`) and manual conversion by hand.

---

## Table of Contents

1. [Overview](#overview)
2. [Characteristic Mapping](#characteristic-mapping)
3. [Trigger Mapping](#trigger-mapping)
4. [Automation Patterns](#automation-patterns)
5. [Entity Mapping Strategy](#entity-mapping-strategy)
6. [Party Mode and Global Conditions](#party-mode-and-global-conditions)
7. [Common Issues](#common-issues)
8. [Example Conversion](#example-conversion)

---

## Overview

### What the Conversion Pipeline Does

The conversion pipeline reads the merged HomeKit JSON export (produced by `merge_exports.py`) and generates Home Assistant automation YAML. It handles three main tasks:

1. **Entity mapping** -- matching HomeKit accessory names to Home Assistant `entity_id` values using the HA entity registry (`core.entity_registry`).
2. **Characteristic translation** -- converting HomeKit characteristic write actions (identified by UUID) into the corresponding HA service calls with correct parameters.
3. **Logic preservation** -- converting HomeKit automation structures (triggers, conditions, and action flows including shortcut-based conditionals) into equivalent HA automation YAML.

### Two Types of Automations

HomeKit automations fall into two categories, and each converts differently:

#### Direct-Write Automations (~55% of typical setups)

These automations contain only `CharacteristicWriteAction` entries -- simple trigger-to-action mappings with no conditional logic. A trigger fires and a list of characteristic values is written to accessories.

**HomeKit structure:**
```
Trigger → ActionSet → [CharacteristicWriteAction, CharacteristicWriteAction, ...]
```

**HA structure:**
```yaml
trigger: ...
action:
  - service: light.turn_on
    target:
      entity_id: light.bedroom
  - service: light.turn_off
    target:
      entity_id: light.hallway
```

These convert cleanly with no ambiguity.

#### Shortcut Automations (~45% of typical setups)

These automations use Apple's "Convert to Shortcut" feature, which wraps actions in a Shortcuts workflow. The workflow can contain conditional blocks (if/else), multi-branch menus, nested conditions, and device actions encoded in protobuf. The public HomeKit API (`HMHomeManager`) exposes these as opaque `HMShortcutAction` objects with no accessible internals.

The homed database extraction recovers the full workflow, which the conversion pipeline then translates into HA `choose` blocks, `conditions`, and nested action structures.

**HomeKit structure:**
```
Trigger → ShortcutAction → WFWorkflowActions:
  conditional (if)
    homeaccessory → [write actions]
  conditional (else)
    homeaccessory → [write actions]
  conditional (end)
```

**HA structure:**
```yaml
trigger: ...
action:
  - choose:
      - conditions:
          - condition: state
            entity_id: light.bedroom
            state: "on"
        sequence:
          - service: light.turn_off
            target:
              entity_id: light.bedroom
    default:
      - service: light.turn_on
        target:
          entity_id: light.bedroom
```

---

## Characteristic Mapping

HomeKit identifies device capabilities using standardized characteristic UUIDs (defined in the HAP specification). Each characteristic maps to a specific Home Assistant service call and parameter.

### Complete Reference Table

| HomeKit Characteristic | UUID Suffix | HA Domain | HA Service | HA Parameters |
|---|---|---|---|---|
| Power State | `0x0025` | `light` / `switch` / `fan` | `turn_on` / `turn_off` | -- |
| Brightness | `0x0008` | `light` | `turn_on` | `brightness_pct: <0-100>` |
| Hue | `0x0013` | `light` | `turn_on` | `hs_color: [<hue>, ...]` |
| Saturation | `0x002F` | `light` | `turn_on` | `hs_color: [..., <sat>]` |
| Color Temperature | `0x00CE` | `light` | `turn_on` | `color_temp: <mireds>` |
| Target Position | `0x007C` | `cover` | `set_cover_position` | `position: <0-100>` |
| Lock Target State | `0x001E` | `lock` | `lock` / `unlock` | -- |
| Active | `0x00B0` | `fan` / `switch` | `turn_on` / `turn_off` | -- |
| Rotation Speed | `0x0029` | `fan` | `set_percentage` | `percentage: <0-100>` |
| Target Temperature | `0x0035` | `climate` | `set_temperature` | `temperature: <value>` |

### How to Read the UUID

HomeKit characteristic UUIDs follow the pattern `000000XX-0000-1000-8000-0026BB765291` where `XX` is the suffix listed above. For example, the full UUID for Brightness is `00000008-0000-1000-8000-0026BB765291`.

In the merged JSON export, characteristics appear either as the full UUID or as a human-readable name (`"Brightness"`, `"Power State"`, etc.) depending on which extraction method provided the data.

### Boolean Characteristics

For Power State (`0x0025`), Active (`0x00B0`), and Lock Target State (`0x001E`), the target value determines which service to call:

| Target Value | Power State Service | Lock Target State Service |
|---|---|---|
| `true` / `1` | `turn_on` | `lock` (secured) |
| `false` / `0` | `turn_off` | `unlock` (unsecured) |

### Combining Hue and Saturation

HomeKit sets hue and saturation as separate characteristic writes. Home Assistant expects them together as `hs_color: [hue, saturation]`. When converting, collect all characteristic writes for the same accessory and merge hue + saturation into a single service call:

```yaml
# Two HomeKit writes:
#   Hue = 240 (on accessory "Desk Light")
#   Saturation = 80 (on accessory "Desk Light")
# Become one HA call:
- service: light.turn_on
  target:
    entity_id: light.desk_light
  data:
    hs_color: [240, 80]
```

### Color Temperature Units

HomeKit and Home Assistant both use **mireds** (reciprocal megakelvins) for color temperature. No conversion is needed. The formula is:

```
mireds = 1,000,000 / kelvin
```

Common values: 153 mireds = 6535K (cool white), 500 mireds = 2000K (warm white).

---

## Trigger Mapping

HomeKit triggers map to Home Assistant trigger platforms as follows:

### HMTimerTrigger -> platform: time

A HomeKit timer trigger fires at a specific time of day, optionally repeating on certain days.

**HomeKit JSON:**
```json
{
  "triggerType": "timer",
  "fireDate": "2024-01-15T22:30:00Z",
  "recurrence": {
    "daysOfWeek": [1, 2, 3, 4, 5]
  }
}
```

**HA YAML:**
```yaml
trigger:
  - platform: time
    at: "22:30:00"
# If specific days are needed, add a condition:
condition:
  - condition: time
    weekday:
      - mon
      - tue
      - wed
      - thu
      - fri
```

Note: HomeKit uses ISO weekday numbers (1=Monday through 7=Sunday). HA uses three-letter abbreviations.

### HMSignificantTimeEvent -> platform: sun

HomeKit significant time events correspond to sunrise or sunset, optionally with an offset.

**HomeKit JSON:**
```json
{
  "triggerType": "event",
  "events": [
    {
      "eventType": "significantTimeEvent",
      "significantEvent": "sunrise",
      "offset": -1800
    }
  ]
}
```

**HA YAML:**
```yaml
trigger:
  - platform: sun
    event: sunrise
    offset: "-00:30:00"
```

The offset is in seconds in HomeKit and in `HH:MM:SS` format (with sign) in HA.

### HMCharacteristicValueEvent (Programmable Switch) -> device trigger

Button presses in HomeKit appear as characteristic value events on the `Programmable Switch Event` characteristic (`0x0073`). These **do not** have a universal HA equivalent -- the trigger format depends on your integration.

**HomeKit JSON:**
```json
{
  "eventType": "charValue",
  "characteristic": "Programmable Switch Event",
  "accessory": "Bedroom Button",
  "eventValue": 0
}
```

HomeKit event values: `0` = single press, `1` = double press, `2` = long press.

**HA YAML (varies by integration):**

For ZHA (Zigbee):
```yaml
trigger:
  - platform: device
    domain: zha
    device_id: "<device_id>"
    type: remote_button_short_press
    subtype: button_1
```

For MQTT (Zigbee2MQTT):
```yaml
trigger:
  - platform: mqtt
    topic: "zigbee2mqtt/Bedroom Button/action"
    payload: "single"
```

For Matter:
```yaml
trigger:
  - platform: device
    domain: matter
    device_id: "<device_id>"
    type: press
    subtype: 1
```

Button triggers always require manual configuration because the `device_id` and trigger format are integration-specific. The conversion script outputs a placeholder with the accessory name as a comment.

### HMCharacteristicValueEvent (Motion/Occupancy) -> platform: state

Motion sensors and occupancy sensors trigger on characteristic value changes.

**HomeKit JSON:**
```json
{
  "eventType": "charValue",
  "characteristic": "Motion Detected",
  "accessory": "Hallway Motion",
  "eventValue": true
}
```

**HA YAML:**
```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.hallway_motion_occupancy
    to: "on"
```

For occupancy sensors (`Occupancy Detected`, `0x0071`), the mapping is the same but the entity typically ends in `_occupancy`.

### HMLocationEvent -> platform: zone

HomeKit location events fire when a user enters or leaves a geographic region.

**HomeKit JSON:**
```json
{
  "eventType": "location",
  "region": "Home",
  "notifyOnEntry": true,
  "notifyOnExit": false
}
```

**HA YAML:**
```yaml
trigger:
  - platform: zone
    entity_id: person.your_name
    zone: zone.home
    event: enter
```

Note: You must have a `person` entity configured in HA with a device tracker for location-based triggers to work.

### HMPresenceEvent -> platform: state (person entity)

HomeKit presence events (somebody is home / nobody is home) map to state checks on the person or group entity.

**HomeKit JSON:**
```json
{
  "eventType": "presence",
  "presenceType": "anyUserAtHome"
}
```

**HA YAML (somebody home):**
```yaml
trigger:
  - platform: state
    entity_id: group.family
    to: "home"
```

**HA YAML (nobody home):**
```yaml
trigger:
  - platform: state
    entity_id: group.family
    to: "not_home"
```

Alternatively, use individual `person.xxx` entities if you need per-user triggers.

---

## Automation Patterns

### Direct Write

The simplest pattern. A trigger fires and one or more characteristics are written to accessories. No conditional logic.

**HomeKit structure:**
```
Trigger fires -> Write brightness=80 to "Desk Light"
                 Write power=true to "Floor Lamp"
```

**HA YAML:**
```yaml
alias: "Evening Lights"
trigger:
  - platform: sun
    event: sunset
action:
  - service: light.turn_on
    target:
      entity_id: light.desk_light
    data:
      brightness_pct: 80
  - service: light.turn_on
    target:
      entity_id: light.floor_lamp
```

### Toggle (If/Else)

The most common shortcut pattern (~40% of automations). Checks if a device is currently on or off, then does the opposite. Used for single-button toggle control.

**HomeKit workflow steps:**
```
conditional (if) -> check accessory state
  homeaccessory -> turn off
conditional (else)
  homeaccessory -> turn on
conditional (end)
```

**HA YAML using `choose`:**
```yaml
alias: "Bedroom Button Toggle"
trigger:
  # Button press trigger (integration-specific)
  - platform: device
    domain: zha
    device_id: "abc123"
    type: remote_button_short_press
    subtype: button_1
action:
  - choose:
      - conditions:
          - condition: state
            entity_id: light.bedroom
            state: "on"
        sequence:
          - service: light.turn_off
            target:
              entity_id: light.bedroom
    default:
      - service: light.turn_on
        target:
          entity_id: light.bedroom
        data:
          brightness_pct: 100
```

The `choose` block is the HA equivalent of if/else. The first matching condition's `sequence` runs. If no conditions match, `default` runs.

### Conditional (If with No Else)

Some automations check a condition and only act if it is true, with no alternative action.

**HomeKit workflow steps:**
```
conditional (if) -> check time of day
  homeaccessory -> dim lights to 20%
conditional (end)
```

**HA YAML -- two approaches:**

Using automation-level conditions:
```yaml
alias: "Dim Lights at Night Only"
trigger:
  - platform: state
    entity_id: binary_sensor.hallway_motion
    to: "on"
condition:
  - condition: time
    after: "22:00:00"
    before: "06:00:00"
action:
  - service: light.turn_on
    target:
      entity_id: light.hallway
    data:
      brightness_pct: 20
```

Using `choose` with no default:
```yaml
action:
  - choose:
      - conditions:
          - condition: time
            after: "22:00:00"
            before: "06:00:00"
        sequence:
          - service: light.turn_on
            target:
              entity_id: light.hallway
            data:
              brightness_pct: 20
```

Use the automation-level `condition` approach when the entire automation should be gated. Use `choose` when only part of the action list is conditional.

### Nested IF

Multiple levels of conditional logic. Check one condition, then within that branch, check another.

**HomeKit workflow steps:**
```
conditional (if) -> is light on?
  conditional (if) -> is brightness > 50?
    homeaccessory -> set brightness to 20
  conditional (else)
    homeaccessory -> turn off
  conditional (end)
conditional (else)
  homeaccessory -> set brightness to 80
conditional (end)
```

**HA YAML:**
```yaml
action:
  - choose:
      - conditions:
          - condition: state
            entity_id: light.living_room
            state: "on"
        sequence:
          - choose:
              - conditions:
                  - condition: numeric_state
                    entity_id: light.living_room
                    attribute: brightness
                    above: 127
                sequence:
                  - service: light.turn_on
                    target:
                      entity_id: light.living_room
                    data:
                      brightness_pct: 20
            default:
              - service: light.turn_off
                target:
                  entity_id: light.living_room
    default:
      - service: light.turn_on
        target:
          entity_id: light.living_room
        data:
          brightness_pct: 80
```

Nested `choose` blocks mirror nested if/else structures directly. There is no depth limit in HA, but deeply nested automations are hard to maintain -- consider splitting into multiple automations or using scripts.

### Menu (Choose From Menu)

HomeKit's `choosefrommenu` action provides multi-branch selection, equivalent to a switch/case statement. This is rare (~1% of automations) and typically seen in complex scene-selection automations.

**HomeKit workflow steps:**
```
choosefrommenu (start) -> "Which Scene?"
  choosefrommenu (case) -> "Relax"
    homeaccessory -> dim lights, warm color
  choosefrommenu (case) -> "Movie"
    homeaccessory -> lights off, TV backlight on
  choosefrommenu (case) -> "Bright"
    homeaccessory -> all lights 100%
choosefrommenu (end)
```

**HA YAML:**
```yaml
action:
  - choose:
      - conditions:
          - condition: state
            entity_id: input_select.scene_mode
            state: "Relax"
        sequence:
          - service: light.turn_on
            target:
              entity_id: light.living_room
            data:
              brightness_pct: 30
              color_temp: 400
      - conditions:
          - condition: state
            entity_id: input_select.scene_mode
            state: "Movie"
        sequence:
          - service: light.turn_off
            target:
              entity_id: light.living_room
          - service: light.turn_on
            target:
              entity_id: light.tv_backlight
      - conditions:
          - condition: state
            entity_id: input_select.scene_mode
            state: "Bright"
        sequence:
          - service: light.turn_on
            target:
              entity_id: light.living_room
            data:
              brightness_pct: 100
```

Menu-based automations usually require creating an `input_select` helper in HA to serve as the selection variable, since HomeKit's menu mechanism is interactive (it presents choices on screen) while HA automations are non-interactive.

---

## Entity Mapping Strategy

The biggest challenge in conversion is matching HomeKit accessory names to Home Assistant entity IDs. HomeKit uses user-assigned names ("Desk Light"), while HA uses programmatic IDs (`light.desk_light`).

### Mapping Approaches (in order of priority)

The conversion script (`entity_mapper.py`) tries these strategies in order, using the first match it finds:

#### 1. Exact Name Match

Compare the HomeKit accessory name directly against HA entity `friendly_name` values.

```
HomeKit: "Bedroom Lamp"
HA entity: light.bedroom_lamp (friendly_name: "Bedroom Lamp")
Result: exact match
```

#### 2. Normalized Name Match

Convert both names to lowercase, strip special characters, and replace spaces with underscores.

```
HomeKit: "Desk Light (Left)"
Normalized: "desk_light_left"
HA entity: light.desk_light_left
Result: normalized match
```

Normalization rules:
- Lowercase everything
- Remove parentheses, apostrophes, periods, commas
- Replace spaces and hyphens with underscores
- Collapse multiple underscores to one
- Strip leading/trailing underscores

#### 3. Substring Match

Check if the normalized HomeKit name is contained within an HA entity ID (or vice versa).

```
HomeKit: "Kitchen"
HA entity: light.kitchen_ceiling (friendly_name: "Kitchen Ceiling")
Result: substring match (with lower confidence)
```

Substring matches may be ambiguous if multiple entities contain the same substring. When multiple matches are found, the script flags them for manual review.

#### 4. Fuzzy Match (difflib.SequenceMatcher)

Use Python's `difflib.SequenceMatcher` to compute a similarity ratio between names. Accept matches above a threshold (typically 0.75).

```
HomeKit: "Lounge Light"
HA entity: light.living_room_light (friendly_name: "Living Room Light")
Similarity: 0.62 -- below threshold, no match

HomeKit: "Bedrm Lamp"
HA entity: light.bedroom_lamp (friendly_name: "Bedroom Lamp")
Similarity: 0.82 -- above threshold, fuzzy match
```

#### 5. Room-Prefix Stripping

HomeKit users often name accessories with their room as a prefix. Try stripping the room name and matching on the remainder.

```
HomeKit accessory: "Kitchen Pendant"
HomeKit room: "Kitchen"
Stripped name: "Pendant"
HA entity: light.pendant (friendly_name: "Pendant")
Result: match after room-prefix stripping
```

This also handles the reverse case where HA entities include room prefixes but HomeKit names do not.

#### 6. Manual Mapping (Fallback)

When automatic matching fails, the script outputs unmapped accessories as comments in the YAML:

```yaml
# UNMAPPED: HomeKit accessory "Smart Plug 3" has no matching HA entity.
# Add a manual mapping in entity_mapper.py or fix the entity name in HA.
```

To add manual mappings, create a dictionary in `entity_mapper.py`:

```python
MANUAL_MAP = {
    "Smart Plug 3": "switch.office_heater",
    "Old Lamp": "light.vintage_lamp",
    "Hub Sensor": "sensor.zigbee_hub_temperature",
}
```

### Tips for Improving Match Rates

- **Rename HA entities** to match HomeKit names before running the conversion (easier than the reverse).
- **Use the HA entity registry** (`/config/.storage/core.entity_registry`) rather than state entity IDs, since the registry contains the canonical friendly names.
- **Run the mapper first** with `--dry-run` to see the match report before generating YAML.
- **One HomeKit accessory may map to multiple HA entities** -- for example, a multi-sensor (temperature + humidity + motion) becomes three separate entities in HA. The mapper handles this by matching against all entities for each accessory service.

---

## Party Mode and Global Conditions

HomeKit has no native concept of "modes" -- you cannot say "disable all motion-triggered automations when party mode is on." This is a common pain point that the conversion to HA can solve.

### Creating a Party Mode Input

In HA, create an `input_boolean` helper:

```yaml
# configuration.yaml (or via UI: Settings > Devices > Helpers > Toggle)
input_boolean:
  party_mode:
    name: Party Mode
    icon: mdi:party-popper
```

### Adding Party Mode as a Global Condition

For every motion-triggered or occupancy-triggered automation, add a condition that checks party mode:

```yaml
alias: "Hallway Motion - Lights On"
trigger:
  - platform: state
    entity_id: binary_sensor.hallway_motion
    to: "on"
condition:
  - condition: state
    entity_id: input_boolean.party_mode
    state: "off"
action:
  - service: light.turn_on
    target:
      entity_id: light.hallway
    data:
      brightness_pct: 80
```

When `party_mode` is `on`, the condition fails and the automation does not execute.

### Which Automations to Gate

Apply the party mode condition to automations whose triggers are:
- Motion sensors (`binary_sensor.*_motion*`, `binary_sensor.*_occupancy*`)
- Occupancy sensors
- Presence-based triggers (arriving/leaving home)

Do **not** apply party mode to:
- Button-press automations (manual control should always work)
- Time-based automations (scheduled events like "turn off all lights at 2 AM")
- Security automations (locks, alarms)

### Other Global Conditions

The same pattern works for any mode:

```yaml
input_boolean:
  guest_mode:
    name: Guest Mode
  sleep_mode:
    name: Sleep Mode
  vacation_mode:
    name: Vacation Mode
```

The conversion script accepts a `--party-mode-entity` flag to automatically inject the condition into all motion/occupancy automations:

```bash
python3 homekit_to_ha.py \
  --export homekit_merged.json \
  --entity-registry core.entity_registry \
  --party-mode-entity input_boolean.party_mode \
  -o homekit_automations.yaml
```

---

## Common Issues

### Button triggers need manual device trigger configuration

**Problem:** HomeKit uses a standardized `Programmable Switch Event` characteristic for all buttons. Home Assistant does not -- button trigger format depends entirely on the integration (ZHA, Z2M, MQTT, Matter, deCONZ, etc.).

**Solution:** The conversion script outputs button triggers as comments with the accessory name. You must manually configure each one:

1. Go to HA Developer Tools > Events, subscribe to `zha_event` (or `mqtt`) and press the button.
2. Note the `device_id`, `type`, and `subtype` from the event data.
3. Replace the placeholder trigger in the YAML.

**Alternative:** Use the HA automation UI to create the trigger (press the button when prompted), then copy the trigger YAML into your automation file.

### Color temperature units

**Problem:** You might expect a unit conversion between HomeKit and HA color temperature values.

**Non-issue:** Both HomeKit and Home Assistant use mireds (reciprocal megakelvins). A value of 350 in HomeKit is 350 mireds in HA. No conversion is needed.

If you see Kelvin values in your HomeKit export (unlikely but possible in some third-party accessories), convert with: `mireds = 1000000 / kelvin`.

### One HomeKit accessory maps to multiple HA entities

**Problem:** A HomeKit accessory like "Bathroom Multisensor" exposes temperature, humidity, motion, and light level as separate services within one accessory. In HA, these appear as four separate entities (`sensor.bathroom_temperature`, `sensor.bathroom_humidity`, `binary_sensor.bathroom_motion`, `sensor.bathroom_light_level`).

**Solution:** The entity mapper matches against individual HA entities, not devices. When a HomeKit automation writes to a specific characteristic (e.g., `Motion Detected`), the mapper looks for the corresponding HA entity type (`binary_sensor`) with a matching name. If you get wrong matches, add manual mappings.

### Scene-only references may need manual HA scene creation

**Problem:** Some HomeKit automations reference scenes ("Good Night", "Movie Time") rather than individual device actions. The merged export includes scene definitions with their action lists, but HA scenes must be created separately.

**Solution:** Two approaches:

1. **Convert scenes to HA scenes:** Create matching scenes in HA with the same device states. Then reference them in automations:
   ```yaml
   action:
     - service: scene.turn_on
       target:
         entity_id: scene.good_night
   ```

2. **Inline the scene actions:** Expand the scene's action list directly into the automation. This is what the conversion script does by default, since it avoids the need to create and maintain separate scene entities.

### Automation names may collide

**Problem:** HomeKit allows duplicate automation names. HA requires unique `alias` values within the automations file.

**Solution:** The conversion script appends a numeric suffix to duplicate names: `"Bedroom Button"`, `"Bedroom Button 2"`, `"Bedroom Button 3"`.

### Unsupported trigger types

**Problem:** Some HomeKit trigger features have no direct HA equivalent, such as `HMDurationEvent` (trigger after a device has been in a state for X seconds).

**Solution:** Use HA's `for` parameter on state triggers:
```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.front_door
    to: "on"
    for:
      minutes: 5
```

### Brightness scale differences

**Problem:** HomeKit uses 0-100 for brightness percentage. HA's `brightness` attribute uses 0-255 internally, but `brightness_pct` accepts 0-100.

**Solution:** Always use `brightness_pct` (not `brightness`) in HA service calls when converting from HomeKit. The conversion script does this automatically:

```yaml
# Correct:
data:
  brightness_pct: 80

# Wrong (would set brightness to ~31%):
data:
  brightness: 80
```

### Cover/blind position is inverted for some integrations

**Problem:** HomeKit defines position 0 as fully closed and 100 as fully open. Most HA cover integrations use the same convention, but some invert it.

**Solution:** If your blinds open when they should close after conversion, invert the position:

```yaml
data:
  position: "{{ 100 - 75 }}"  # HomeKit target was 75
```

Check your cover entity's behavior in HA Developer Tools > States before running the conversion.

---

## Example Conversion

This section walks through converting a real automation from HomeKit JSON to HA YAML, step by step.

### The HomeKit Automation

A bedroom button that toggles the bedroom light -- single press turns the light on if it is off (to 80% brightness, warm white) or turns it off if it is on. The button also triggers a hallway light to a dim level regardless of state.

**Merged JSON export:**
```json
{
  "name": "Bedroom Button Single Press",
  "enabled": true,
  "triggerType": "event",
  "events": [
    {
      "eventType": "charValue",
      "characteristic": "Programmable Switch Event",
      "characteristicUUID": "00000073-0000-1000-8000-0026BB765291",
      "accessory": "Bedroom Button",
      "eventValue": 0
    }
  ],
  "actionSets": [
    {
      "actions": [
        {
          "actionType": "shortcut",
          "workflowSteps": [
            {
              "type": "conditional",
              "mode": "if",
              "condition": "is",
              "inputType": "HomeAccessory",
              "targetAccessory": "Bedroom Light"
            },
            {
              "type": "homeaccessory",
              "accessoryName": "Bedroom Light",
              "actionSets": [
                {
                  "writeActions": [
                    {
                      "characteristicType": "Power State",
                      "characteristicUUID": "00000025-0000-1000-8000-0026BB765291",
                      "targetValue": false
                    }
                  ]
                }
              ]
            },
            {
              "type": "conditional",
              "mode": "else"
            },
            {
              "type": "homeaccessory",
              "accessoryName": "Bedroom Light",
              "actionSets": [
                {
                  "writeActions": [
                    {
                      "characteristicType": "Power State",
                      "characteristicUUID": "00000025-0000-1000-8000-0026BB765291",
                      "targetValue": true
                    },
                    {
                      "characteristicType": "Brightness",
                      "characteristicUUID": "00000008-0000-1000-8000-0026BB765291",
                      "targetValue": 80
                    },
                    {
                      "characteristicType": "Color Temperature",
                      "characteristicUUID": "000000CE-0000-1000-8000-0026BB765291",
                      "targetValue": 400
                    }
                  ]
                }
              ]
            },
            {
              "type": "conditional",
              "mode": "end"
            },
            {
              "type": "homeaccessory",
              "accessoryName": "Hallway Light",
              "actionSets": [
                {
                  "writeActions": [
                    {
                      "characteristicType": "Power State",
                      "characteristicUUID": "00000025-0000-1000-8000-0026BB765291",
                      "targetValue": true
                    },
                    {
                      "characteristicType": "Brightness",
                      "characteristicUUID": "00000008-0000-1000-8000-0026BB765291",
                      "targetValue": 15
                    }
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

### Step 1: Identify the Trigger

The trigger is a `Programmable Switch Event` with `eventValue: 0` (single press) on `"Bedroom Button"`.

This is a button trigger, so it needs manual configuration. Look up your button's integration. For this example, assume ZHA with device ID `abc123def456`:

```yaml
trigger:
  - platform: device
    domain: zha
    device_id: "abc123def456"
    type: remote_button_short_press
    subtype: button_1
```

### Step 2: Map Entities

Look up each accessory name in the HA entity registry:

| HomeKit Accessory | HA Entity ID |
|---|---|
| Bedroom Light | `light.bedroom_light` |
| Hallway Light | `light.hallway_light` |

### Step 3: Parse the Workflow Structure

Reading the `workflowSteps` array in order:

1. `conditional (if)` -- check if "Bedroom Light" is on
2. `homeaccessory` -- Bedroom Light: Power State = false (turn off)
3. `conditional (else)` -- otherwise...
4. `homeaccessory` -- Bedroom Light: Power State = true, Brightness = 80, Color Temp = 400
5. `conditional (end)` -- end of if/else
6. `homeaccessory` -- Hallway Light: Power State = true, Brightness = 15 (runs always, outside the conditional)

This is a toggle pattern with an unconditional action at the end.

### Step 4: Translate Characteristics

For the "turn off" branch (step 2):
- Power State = false -> `light.turn_off`

For the "turn on" branch (step 4):
- Power State = true -> `light.turn_on`
- Brightness = 80 -> `brightness_pct: 80`
- Color Temperature = 400 -> `color_temp: 400`
- All three target the same accessory, so merge into one service call.

For the hallway light (step 6):
- Power State = true -> `light.turn_on`
- Brightness = 15 -> `brightness_pct: 15`
- Merge into one service call.

### Step 5: Assemble the HA YAML

```yaml
- alias: "Bedroom Button Single Press"
  id: "bedroom_button_single_press"
  trigger:
    # TODO: Replace with actual device trigger for "Bedroom Button"
    # Integration: ZHA/Z2M/Matter -- press button and check event in Developer Tools
    - platform: device
      domain: zha
      device_id: "abc123def456"
      type: remote_button_short_press
      subtype: button_1
  action:
    # Toggle bedroom light (if on -> off, else -> on at 80% warm white)
    - choose:
        - conditions:
            - condition: state
              entity_id: light.bedroom_light
              state: "on"
          sequence:
            - service: light.turn_off
              target:
                entity_id: light.bedroom_light
      default:
        - service: light.turn_on
          target:
            entity_id: light.bedroom_light
          data:
            brightness_pct: 80
            color_temp: 400
    # Always set hallway light to dim (runs regardless of bedroom state)
    - service: light.turn_on
      target:
        entity_id: light.hallway_light
      data:
        brightness_pct: 15
```

### Step 6: Validate

1. **Check entity IDs** -- verify `light.bedroom_light` and `light.hallway_light` exist in HA (Developer Tools > States).
2. **Check the trigger** -- press the button and confirm the automation fires.
3. **Test both branches** -- with the bedroom light on, press the button (should turn off). With it off, press again (should turn on to 80%, warm white). The hallway light should go to 15% both times.
4. **Check the HA automation editor** -- paste the YAML into Settings > Automations > Create Automation > Edit in YAML to validate syntax.

### What the Script Does Automatically

When you run `homekit_to_ha.py`, it performs steps 1-5 for every automation in the merged export:

1. Parses all triggers and converts them (with placeholders for button triggers).
2. Runs the entity mapper against your HA entity registry.
3. Walks the workflow steps, building nested `choose` blocks for conditionals.
4. Merges characteristic writes into combined service calls.
5. Outputs a single YAML file with all automations, plus comments for anything that needs manual attention.

The script's output is valid HA automation YAML that you can paste directly into `automations.yaml` or import through the HA UI. Button triggers and unmapped entities will be marked with `# TODO` comments.
