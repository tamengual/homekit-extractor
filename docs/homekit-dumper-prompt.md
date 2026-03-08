# Claude Code Prompt: Build HomeKit Automation Dumper App

## Goal

Build a minimal Mac Catalyst app (or iOS app) that uses Apple's HomeKit framework to enumerate ALL automations, scenes, accessories, and rooms in my HomeKit home, then exports everything to a JSON file I can share. The JSON will be used to recreate all ~200 HomeKit automations in Home Assistant.

## What to Build

A single-view Mac Catalyst app (preferred) or iOS app with:
- One button: "Export HomeKit Data"
- Uses `HMHomeManager` to access HomeKit
- Dumps ALL data to a pretty-printed JSON file saved to the Documents directory (accessible via Files app on iOS or Finder on Mac)
- Shows a status label with progress and the output file path when done

## Required Entitlements

- Add the `com.apple.developer.homekit` entitlement
- Add `NSHomeKitUsageDescription` to Info.plist with a description like "This app reads your HomeKit setup to export automation data"
- Use development signing (free Apple Developer account is fine)

## Data to Extract

### 1. Automations (MOST IMPORTANT)
From `home.triggers`, extract every `HMTrigger`:

For ALL triggers:
- `name` (automation name)
- `uniqueIdentifier`
- `isEnabled`
- `actionSets` — for each `HMActionSet`:
  - `name`
  - `actions` — for each `HMCharacteristicWriteAction`:
    - Accessory name (`action.characteristic.service?.accessory?.name`)
    - Service name (`action.characteristic.service?.name`)
    - Characteristic description (`action.characteristic.localizedDescription`)
    - Characteristic type UUID (`action.characteristic.characteristicType`)
    - Target value (`action.targetValue`)
    - Room name (`action.characteristic.service?.accessory?.room?.name`)

For `HMEventTrigger`:
- `events` array — for each event:
  - If `HMCharacteristicEvent`: characteristic info + trigger value
  - If `HMLocationEvent`: region identifier, center, radius
  - If `HMPresenceEvent`: presence type + user type (raw values)
  - If `HMSignificantTimeEvent`: significant event type (sunrise/sunset) + offset
  - If `HMCalendarEvent`: fire date components
  - If `HMDurationEvent`: duration
- `endEvents` array (same format)
- `predicate` — decompose the `NSPredicate`:
  - If `NSCompoundPredicate`: recursively extract subpredicates
  - If `NSComparisonPredicate`: extract left expression key path, operator, right expression constant value
  - Store the raw `predicate.description` string as fallback
- `executeOnce` flag
- `triggerActivationState`

For `HMTimerTrigger`:
- `fireDate`
- `timeZone`
- `recurrence` (date components)
- `recurrenceCalendar`

### 2. Scenes
From `home.actionSets`, extract each `HMActionSet`:
- `name`
- `uniqueIdentifier`
- `actionSetType` (e.g., `.homeArrival`, `.homeDeparture`, `.sleep`, `.wakeUp`, `.userDefined`)
- All actions (same format as automation actions above)

### 3. Rooms
From `home.rooms`:
- `name`
- `uniqueIdentifier`
- List of accessory names in each room

### 4. Accessories
From `home.accessories`:
- `name`
- `uniqueIdentifier`
- `room?.name`
- `manufacturer`
- `model`
- `firmwareVersion`
- `isReachable`
- `category.categoryType` and `category.localizedDescription`
- For each service:
  - `name`
  - `serviceType`
  - `localizedDescription`
  - For each characteristic:
    - `localizedDescription`
    - `characteristicType`
    - `value` (current value)
    - `properties` (readable, writable, supportsEvent)
    - `metadata` (min, max, step, units if available)

## JSON Output Structure

```json
{
  "exportDate": "2026-03-08T12:00:00Z",
  "homeName": "My Home",
  "rooms": [...],
  "accessories": [...],
  "scenes": [...],
  "automations": [
    {
      "name": "Automation Name",
      "uniqueIdentifier": "UUID",
      "enabled": true,
      "type": "event|timer",
      "trigger": {
        "events": [...],
        "endEvents": [...],
        "predicate": "...",
        "predicateStructured": {...}
      },
      "actionSets": [
        {
          "name": "Scene Name",
          "actions": [
            {
              "accessoryName": "Kitchen lamp 1",
              "roomName": "Kitchen",
              "serviceName": "Kitchen lamp 1",
              "characteristic": "Brightness",
              "characteristicType": "00000008-...",
              "targetValue": 100
            }
          ]
        }
      ]
    }
  ]
}
```

## NSPredicate Decomposition

This is critical — HomeKit conditions are encoded as NSPredicates. Decompose them recursively:

```swift
func decomposePredicate(_ predicate: NSPredicate) -> [String: Any] {
    var result: [String: Any] = ["raw": predicate.description]

    if let compound = predicate as? NSCompoundPredicate {
        result["type"] = "compound"
        result["operator"] = compound.compoundPredicateType == .and ? "AND" :
                             compound.compoundPredicateType == .or ? "OR" : "NOT"
        result["subpredicates"] = compound.subpredicates.compactMap {
            ($0 as? NSPredicate).map { decomposePredicate($0) }
        }
    } else if let comparison = predicate as? NSComparisonPredicate {
        result["type"] = "comparison"
        result["leftKeyPath"] = comparison.leftExpression.keyPath
        result["operator"] = String(describing: comparison.predicateOperatorType.rawValue)
        result["rightValue"] = "\(comparison.rightExpression.constantValue ?? "nil")"
    }

    return result
}
```

Look at Apple's HMCatalog sample code (`HMEventTrigger+Convenience.swift`) and the FibaroHomeKitTool project (`NSPredicate+Condition.swift`) for reference on parsing HomeKit-specific predicates like sun time conditions and characteristic thresholds.

## Key Technical Notes

- `HMHomeManager` is async — you must wait for `homeManagerDidUpdateHomes(_:)` delegate callback before accessing homes
- Use Mac Catalyst (not native macOS) — HomeKit framework is only available through Catalyst on Mac
- Development signing works fine, no paid Apple Developer Program needed
- The app needs to run once, dump the JSON, then I'm done with it
- Handle nil values gracefully — many properties are optional
- Wrap characteristic values in strings to handle different types (Int, Float, Bool, String)
- Some automations may use `HMActionSet` references to existing scenes rather than inline actions — capture both the scene name/ID and resolve the actual actions

## After Building

1. Run the app on your Mac (Catalyst) or iPhone
2. Grant HomeKit access when prompted
3. Tap "Export HomeKit Data"
4. Find the JSON file in Documents
5. Copy it to your PC (OneDrive, AirDrop, email, etc.)
6. Share the JSON file path and I'll convert all automations to Home Assistant YAML
