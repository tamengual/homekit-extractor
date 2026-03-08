import Foundation
import HomeKit
import CoreLocation
import ObjectiveC

class HomeKitExporter: NSObject, ObservableObject, HMHomeManagerDelegate {
    @Published var statusMessage = "Ready to export"
    @Published var isExporting = false
    @Published var outputFilePath: String?

    private var homeManager: HMHomeManager?

    /// Lookup table: actionSet UUID -> scene data (built once per export)
    private var sceneLookup: [UUID: [String: Any]] = [:]

    func startExport() {
        isExporting = true
        statusMessage = "Initializing HomeKit..."
        outputFilePath = nil
        homeManager = HMHomeManager()
        homeManager?.delegate = self
    }

    // MARK: - HMHomeManagerDelegate

    func homeManagerDidUpdateHomes(_ manager: HMHomeManager) {
        guard let home = manager.primaryHome ?? manager.homes.first else {
            DispatchQueue.main.async {
                self.statusMessage = "No HomeKit home found. Make sure you have a home configured and granted access."
                self.isExporting = false
            }
            return
        }

        DispatchQueue.main.async {
            self.statusMessage = "Found home: \(home.name). Extracting data..."
        }

        DispatchQueue.global(qos: .userInitiated).async {
            // Build scene lookup table first so automations can cross-reference
            self.buildSceneLookup(from: home)

            let data = self.extractAllData(from: home)

            do {
                let jsonData = try JSONSerialization.data(
                    withJSONObject: data,
                    options: [.prettyPrinted, .sortedKeys, .fragmentsAllowed]
                )
                let filePath = self.saveToDocuments(data: jsonData, filename: "homekit_export.json")

                let automationCount = (data["automations"] as? [Any])?.count ?? 0
                let sceneCount = (data["scenes"] as? [Any])?.count ?? 0
                let accessoryCount = (data["accessories"] as? [Any])?.count ?? 0
                let roomCount = (data["rooms"] as? [Any])?.count ?? 0

                DispatchQueue.main.async {
                    self.statusMessage = "Export complete!\n\(home.name): \(automationCount) automations, \(sceneCount) scenes, \(accessoryCount) accessories, \(roomCount) rooms"
                    self.outputFilePath = filePath
                    self.isExporting = false
                }
            } catch {
                DispatchQueue.main.async {
                    self.statusMessage = "Error writing JSON: \(error.localizedDescription)"
                    self.isExporting = false
                }
            }
        }
    }

    // MARK: - Scene Lookup

    /// Pre-build a lookup of all scenes (user-defined action sets) so automations
    /// can cross-reference and inline the full scene contents.
    private func buildSceneLookup(from home: HMHome) {
        sceneLookup = [:]
        for actionSet in home.actionSets {
            let data: [String: Any] = [
                "name": actionSet.name,
                "uniqueIdentifier": actionSet.uniqueIdentifier.uuidString,
                "actionSetType": actionSetTypeString(actionSet.actionSetType),
                "actions": extractActions(from: actionSet)
            ]
            sceneLookup[actionSet.uniqueIdentifier] = data
        }
    }

    // MARK: - Top-Level Extraction

    private func extractAllData(from home: HMHome) -> [String: Any] {
        let formatter = ISO8601DateFormatter()

        return [
            "exportDate": formatter.string(from: Date()),
            "homeName": home.name,
            "homeUniqueIdentifier": home.uniqueIdentifier.uuidString,
            "rooms": extractRooms(from: home),
            "accessories": extractAccessories(from: home),
            "scenes": extractScenes(from: home),
            "automations": extractAutomations(from: home)
        ]
    }

    // MARK: - Rooms

    private func extractRooms(from home: HMHome) -> [[String: Any]] {
        return home.rooms.map { room in
            [
                "name": room.name,
                "uniqueIdentifier": room.uniqueIdentifier.uuidString,
                "accessories": room.accessories.map { $0.name }
            ]
        }
    }

    // MARK: - Accessories

    private func extractAccessories(from home: HMHome) -> [[String: Any]] {
        return home.accessories.map { accessory in
            var dict: [String: Any] = [
                "name": accessory.name,
                "uniqueIdentifier": accessory.uniqueIdentifier.uuidString,
                "isReachable": accessory.isReachable,
                "category": [
                    "categoryType": accessory.category.categoryType,
                    "localizedDescription": accessory.category.localizedDescription
                ] as [String: Any],
                "services": extractServices(from: accessory)
            ]

            if let room = accessory.room {
                dict["roomName"] = room.name
            }
            if let manufacturer = accessory.manufacturer {
                dict["manufacturer"] = manufacturer
            }
            if let model = accessory.model {
                dict["model"] = model
            }
            if let firmware = accessory.firmwareVersion {
                dict["firmwareVersion"] = firmware
            }

            return dict
        }
    }

    private func extractServices(from accessory: HMAccessory) -> [[String: Any]] {
        return accessory.services.map { service in
            [
                "name": service.name,
                "serviceType": service.serviceType,
                "localizedDescription": service.localizedDescription,
                "uniqueIdentifier": service.uniqueIdentifier.uuidString,
                "characteristics": extractCharacteristics(from: service)
            ] as [String: Any]
        }
    }

    private func extractCharacteristics(from service: HMService) -> [[String: Any]] {
        return service.characteristics.map { char in
            var dict: [String: Any] = [
                "localizedDescription": char.localizedDescription,
                "characteristicType": char.characteristicType,
                "properties": char.properties,
                "uniqueIdentifier": char.uniqueIdentifier.uuidString
            ]

            if let value = char.value {
                dict["value"] = sanitizeValue(value)
            }

            if let metadata = char.metadata {
                var meta: [String: Any] = [:]
                if let min = metadata.minimumValue { meta["minimumValue"] = min }
                if let max = metadata.maximumValue { meta["maximumValue"] = max }
                if let step = metadata.stepValue { meta["stepValue"] = step }
                if let units = metadata.units { meta["units"] = units }
                if let format = metadata.format { meta["format"] = format }
                if let maxLen = metadata.maxLength { meta["maxLength"] = maxLen }
                if !meta.isEmpty {
                    dict["metadata"] = meta
                }
            }

            return dict
        }
    }

    // MARK: - Scenes

    private func extractScenes(from home: HMHome) -> [[String: Any]] {
        return home.actionSets.map { actionSet in
            [
                "name": actionSet.name,
                "uniqueIdentifier": actionSet.uniqueIdentifier.uuidString,
                "actionSetType": actionSetTypeString(actionSet.actionSetType),
                "actionCount": actionSet.actions.count,
                "actions": extractActions(from: actionSet)
            ] as [String: Any]
        }
    }

    private func actionSetTypeString(_ type: String) -> String {
        switch type {
        case HMActionSetTypeHomeArrival: return "homeArrival"
        case HMActionSetTypeHomeDeparture: return "homeDeparture"
        case HMActionSetTypeSleep: return "sleep"
        case HMActionSetTypeWakeUp: return "wakeUp"
        case HMActionSetTypeUserDefined: return "userDefined"
        case HMActionSetTypeTriggerOwned: return "triggerOwned"
        default: return type
        }
    }

    // MARK: - Actions (Enhanced)

    private func extractActions(from actionSet: HMActionSet) -> [[String: Any]] {
        return actionSet.actions.map { action -> [String: Any] in
            var dict: [String: Any] = [
                "actionClass": String(describing: type(of: action))
            ]

            if let charAction = action as? HMCharacteristicWriteAction<NSCopying> {
                dict["type"] = "characteristicWrite"
                dict["targetValue"] = sanitizeValue(charAction.targetValue)
                dict["characteristic"] = charAction.characteristic.localizedDescription
                dict["characteristicType"] = charAction.characteristic.characteristicType
                dict["characteristicUniqueIdentifier"] = charAction.characteristic.uniqueIdentifier.uuidString

                if let service = charAction.characteristic.service {
                    dict["serviceName"] = service.name
                    dict["serviceType"] = service.serviceType
                    dict["serviceUniqueIdentifier"] = service.uniqueIdentifier.uuidString

                    if let accessory = service.accessory {
                        dict["accessoryName"] = accessory.name
                        dict["accessoryUniqueIdentifier"] = accessory.uniqueIdentifier.uuidString
                        if let room = accessory.room {
                            dict["roomName"] = room.name
                        }
                    }
                }
            } else {
                // Non-standard action (shortcut action, etc.)
                // Use ObjC runtime to discover and read ALL properties
                dict["type"] = "nonCharacteristic"
                dict["description"] = String(describing: action)

                do {
                    let nsObj = action as NSObject
                    let runtimeProps = extractViaObjCRuntime(nsObj)
                    for (key, value) in runtimeProps {
                        if dict[key] == nil {
                            dict[key] = value
                        }
                    }
                }

                // Also use Mirror for any Swift-only stored properties
                let reflected = reflectObject(action)
                for (key, value) in reflected {
                    if dict[key] == nil {
                        dict[key] = value
                    }
                }
            }

            // Always capture the uniqueIdentifier if available via KVC
            if let uid = safeValue(forKey: "uniqueIdentifier", on: action as NSObject) as? UUID {
                dict["uniqueIdentifier"] = uid.uuidString
            }

            return dict
        }
    }

    // MARK: - Automations (Enhanced)

    private func extractAutomations(from home: HMHome) -> [[String: Any]] {
        return home.triggers.map { trigger in
            var dict: [String: Any] = [
                "name": trigger.name,
                "uniqueIdentifier": trigger.uniqueIdentifier.uuidString,
                "enabled": trigger.isEnabled,
                "lastFireDate": trigger.lastFireDate.map {
                    ISO8601DateFormatter().string(from: $0)
                } as Any
            ]

            // Extract action sets with scene cross-referencing
            dict["actionSets"] = trigger.actionSets.map { actionSet in
                self.extractActionSetForAutomation(actionSet)
            }

            // Detect potential shortcut automations
            let hasNonCharActions = trigger.actionSets.contains { actionSet in
                actionSet.actions.contains { !($0 is HMCharacteristicWriteAction<NSCopying>) }
            }
            let allEmpty = trigger.actionSets.allSatisfy { $0.actions.isEmpty }

            if allEmpty && !trigger.actionSets.isEmpty {
                dict["_note"] = "This automation has action sets with no visible actions. It may use a Shortcuts workflow whose conditional logic is not accessible via the HomeKit API."
                dict["isLikelyShortcut"] = true
            } else if hasNonCharActions {
                dict["hasNonStandardActions"] = true
            }

            if let eventTrigger = trigger as? HMEventTrigger {
                dict["type"] = "event"
                dict["trigger"] = extractEventTrigger(eventTrigger)
            } else if let timerTrigger = trigger as? HMTimerTrigger {
                dict["type"] = "timer"
                dict["trigger"] = extractTimerTrigger(timerTrigger)
            } else {
                dict["type"] = "unknown"
                dict["triggerClass"] = String(describing: type(of: trigger))
                dict["triggerReflection"] = reflectObject(trigger)
            }

            return dict
        }
    }

    /// Extract an action set within an automation, cross-referencing to scenes.
    private func extractActionSetForAutomation(_ actionSet: HMActionSet) -> [String: Any] {
        var asDict: [String: Any] = [
            "name": actionSet.name,
            "uniqueIdentifier": actionSet.uniqueIdentifier.uuidString,
            "actionSetType": actionSetTypeString(actionSet.actionSetType),
            "actionCount": actionSet.actions.count,
            "actions": extractActions(from: actionSet)
        ]

        // If this action set is trigger-owned (inline), check if it references
        // any named scene by name match. Also if it's NOT trigger-owned, it IS
        // a scene reference — inline the full scene actions.
        if actionSet.actionSetType != HMActionSetTypeTriggerOwned {
            asDict["isSceneReference"] = true
            // Include the resolved scene data so the automation JSON is self-contained
            if let sceneData = sceneLookup[actionSet.uniqueIdentifier] {
                asDict["resolvedScene"] = sceneData
            }
        }

        return asDict
    }

    // MARK: - Event Triggers

    private func extractEventTrigger(_ trigger: HMEventTrigger) -> [String: Any] {
        var dict: [String: Any] = [
            "events": extractEvents(trigger.events),
            "executeOnce": trigger.executeOnce,
            "triggerActivationState": triggerActivationStateString(trigger.triggerActivationState)
        ]

        let endEvents = trigger.endEvents
        if !endEvents.isEmpty {
            dict["endEvents"] = extractEvents(endEvents)
        }

        if let predicate = trigger.predicate {
            dict["predicate"] = predicate.description
            dict["predicateStructured"] = decomposePredicate(predicate)
        }

        return dict
    }

    private func triggerActivationStateString(_ state: HMEventTriggerActivationState) -> String {
        switch state {
        case .enabled: return "enabled"
        case .disabled: return "disabled"
        default: return "unknown(\(state.rawValue))"
        }
    }

    private func extractEvents(_ events: [HMEvent]) -> [[String: Any]] {
        return events.map { event -> [String: Any] in
            var dict: [String: Any] = [
                "uniqueIdentifier": event.uniqueIdentifier.uuidString,
                "eventClass": String(describing: type(of: event))
            ]

            if let charEvent = event as? HMCharacteristicEvent<NSCopying> {
                dict["eventType"] = "characteristic"
                dict["characteristic"] = charEvent.characteristic.localizedDescription
                dict["characteristicType"] = charEvent.characteristic.characteristicType
                dict["characteristicUniqueIdentifier"] = charEvent.characteristic.uniqueIdentifier.uuidString

                if let triggerValue = charEvent.triggerValue {
                    dict["triggerValue"] = sanitizeValue(triggerValue)
                }

                if let service = charEvent.characteristic.service {
                    dict["serviceName"] = service.name
                    dict["serviceType"] = service.serviceType
                    if let accessory = service.accessory {
                        dict["accessoryName"] = accessory.name
                        dict["accessoryUniqueIdentifier"] = accessory.uniqueIdentifier.uuidString
                        if let room = accessory.room {
                            dict["roomName"] = room.name
                        }
                    }
                }
            } else if let locationEvent = event as? HMLocationEvent {
                dict["eventType"] = "location"
                if let region = locationEvent.region {
                    dict["regionIdentifier"] = region.identifier
                    if let circular = region as? CLCircularRegion {
                        dict["center"] = [
                            "latitude": circular.center.latitude,
                            "longitude": circular.center.longitude
                        ] as [String: Any]
                        dict["radius"] = circular.radius
                        dict["notifyOnEntry"] = circular.notifyOnEntry
                        dict["notifyOnExit"] = circular.notifyOnExit
                    }
                }
            } else if let presenceEvent = event as? HMPresenceEvent {
                dict["eventType"] = "presence"
                dict["presenceEventType"] = presenceEventTypeString(presenceEvent.presenceEventType)
                dict["presenceEventTypeRaw"] = presenceEvent.presenceEventType.rawValue
                dict["presenceUserType"] = presenceUserTypeString(presenceEvent.presenceUserType)
                dict["presenceUserTypeRaw"] = presenceEvent.presenceUserType.rawValue
            } else if let sigTimeEvent = event as? HMSignificantTimeEvent {
                dict["eventType"] = "significantTime"
                let sigEvent = sigTimeEvent.significantEvent
                if sigEvent == HMSignificantEvent.sunrise {
                    dict["significantEvent"] = "sunrise"
                } else if sigEvent == HMSignificantEvent.sunset {
                    dict["significantEvent"] = "sunset"
                } else {
                    dict["significantEvent"] = sigEvent.rawValue
                }
                if let offset = sigTimeEvent.offset {
                    dict["offset"] = extractDateComponents(offset)
                }
            } else if let calendarEvent = event as? HMCalendarEvent {
                dict["eventType"] = "calendar"
                dict["fireDateComponents"] = extractDateComponents(calendarEvent.fireDateComponents)
            } else if let durationEvent = event as? HMDurationEvent {
                dict["eventType"] = "duration"
                dict["duration"] = durationEvent.duration
            } else {
                // Unknown event type — use reflection to capture everything
                dict["eventType"] = "unknown"
                let reflected = reflectObject(event)
                for (key, value) in reflected {
                    if dict[key] == nil {
                        dict[key] = value
                    }
                }
            }

            return dict
        }
    }

    // MARK: - Timer Triggers

    private func extractTimerTrigger(_ trigger: HMTimerTrigger) -> [String: Any] {
        let formatter = ISO8601DateFormatter()
        var dict: [String: Any] = [:]

        dict["fireDate"] = formatter.string(from: trigger.fireDate)

        if let timeZone = trigger.timeZone {
            dict["timeZone"] = timeZone.identifier
        }

        if let recurrence = trigger.recurrence {
            dict["recurrence"] = extractDateComponents(recurrence)
        }

        if let calendar = trigger.recurrenceCalendar {
            dict["recurrenceCalendar"] = calendar.identifier
        }

        return dict
    }

    // MARK: - Predicate Decomposition

    private func decomposePredicate(_ predicate: NSPredicate) -> [String: Any] {
        var result: [String: Any] = ["raw": predicate.description]

        if let compound = predicate as? NSCompoundPredicate {
            result["type"] = "compound"
            switch compound.compoundPredicateType {
            case .and: result["operator"] = "AND"
            case .or: result["operator"] = "OR"
            case .not: result["operator"] = "NOT"
            @unknown default: result["operator"] = "unknown(\(compound.compoundPredicateType.rawValue))"
            }
            result["subpredicates"] = compound.subpredicates.compactMap {
                ($0 as? NSPredicate).map { decomposePredicate($0) }
            }
        } else if let comparison = predicate as? NSComparisonPredicate {
            result["type"] = "comparison"
            result["leftExpression"] = comparison.leftExpression.description

            if comparison.leftExpression.expressionType == .keyPath {
                result["leftKeyPath"] = comparison.leftExpression.keyPath
            }

            result["operator"] = comparisonOperatorString(comparison.predicateOperatorType)
            result["operatorRawValue"] = comparison.predicateOperatorType.rawValue

            if comparison.rightExpression.expressionType == .constantValue {
                if let val = comparison.rightExpression.constantValue {
                    result["rightValue"] = sanitizeValue(val)
                } else {
                    result["rightValue"] = NSNull()
                }
            } else {
                result["rightExpression"] = comparison.rightExpression.description
            }

            result["modifier"] = comparisonModifierString(comparison.comparisonPredicateModifier)
            result["options"] = comparison.options.rawValue
        }

        return result
    }

    private func comparisonOperatorString(_ op: NSComparisonPredicate.Operator) -> String {
        switch op {
        case .lessThan: return "<"
        case .lessThanOrEqualTo: return "<="
        case .greaterThan: return ">"
        case .greaterThanOrEqualTo: return ">="
        case .equalTo: return "=="
        case .notEqualTo: return "!="
        case .matches: return "MATCHES"
        case .like: return "LIKE"
        case .beginsWith: return "BEGINSWITH"
        case .endsWith: return "ENDSWITH"
        case .in: return "IN"
        case .contains: return "CONTAINS"
        case .between: return "BETWEEN"
        case .customSelector: return "CUSTOM"
        @unknown default: return "unknown(\(op.rawValue))"
        }
    }

    private func comparisonModifierString(_ mod: NSComparisonPredicate.Modifier) -> String {
        switch mod {
        case .direct: return "direct"
        case .all: return "all"
        case .any: return "any"
        @unknown default: return "unknown(\(mod.rawValue))"
        }
    }

    // MARK: - Presence Event Helpers

    private func presenceEventTypeString(_ type: HMPresenceEventType) -> String {
        switch type {
        case .everyEntry: return "everyEntry"
        case .everyExit: return "everyExit"
        case .firstEntry: return "firstEntry"
        case .lastExit: return "lastExit"
        @unknown default: return "unknown(\(type.rawValue))"
        }
    }

    private func presenceUserTypeString(_ type: HMPresenceEventUserType) -> String {
        switch type {
        case .currentUser: return "currentUser"
        case .homeUsers: return "homeUsers"
        case .customUsers: return "customUsers"
        @unknown default: return "unknown(\(type.rawValue))"
        }
    }

    // MARK: - ObjC Runtime Introspection

    /// Use the Objective-C runtime to enumerate ALL declared properties on an object,
    /// including private/internal ones from Apple frameworks like HMShortcutAction.
    /// This is our primary tool for extracting shortcut automation data.
    private func extractViaObjCRuntime(_ obj: NSObject) -> [String: Any] {
        var result: [String: Any] = [:]
        var currentClass: AnyClass? = type(of: obj)

        // Collect class hierarchy
        var classChain: [String] = []
        var tempClass: AnyClass? = currentClass
        while let cls = tempClass {
            classChain.append(NSStringFromClass(cls))
            if cls == NSObject.self { break }
            tempClass = class_getSuperclass(cls)
        }
        result["_classHierarchy"] = classChain

        // Walk the class hierarchy and extract every declared property
        while let cls = currentClass {
            if cls == NSObject.self { break }

            var propertyCount: UInt32 = 0
            if let properties = class_copyPropertyList(cls, &propertyCount) {
                for i in 0..<Int(propertyCount) {
                    let property = properties[i]
                    let name = String(cString: property_getName(property))

                    // Get property attributes for debugging
                    let attrs = property_getAttributes(property).map { String(cString: $0) } ?? ""

                    // Try to read value via safe KVC
                    if let value = safeValue(forKey: name, on: obj) {
                        result[name] = sanitizeValueDeep(value)
                    } else {
                        result["_\(name)_attributes"] = attrs
                    }
                }
                free(properties)
            }

            // Also enumerate methods to find getter-style selectors
            // that might not have matching @property declarations
            var methodCount: UInt32 = 0
            if let methods = class_copyMethodList(cls, &methodCount) {
                var methodNames: [String] = []
                for i in 0..<Int(methodCount) {
                    let sel = method_getName(methods[i])
                    let name = NSStringFromSelector(sel)
                    methodNames.append(name)

                    // Try zero-arg methods that look like getters (no colons)
                    if !name.contains(":") && !name.hasPrefix("_") &&
                       !name.hasPrefix(".") && !name.hasPrefix("init") &&
                       !name.hasPrefix("dealloc") && result[name] == nil {
                        if let value = safeValue(forKey: name, on: obj) {
                            result[name] = sanitizeValueDeep(value)
                        }
                    }
                }
                result["_methods_\(NSStringFromClass(cls))"] = methodNames
                free(methods)
            }

            currentClass = class_getSuperclass(cls)
        }

        return result
    }

    /// Safely read a value via KVC, returning nil if the key is undefined.
    /// Uses an ObjC helper that catches NSUndefinedKeyException.
    private func safeValue(forKey key: String, on obj: NSObject) -> Any? {
        return SafeKVCReader.value(forKey: key, on: obj)
    }

    /// Deep sanitize that handles complex nested objects from runtime extraction.
    private func sanitizeValueDeep(_ value: Any) -> Any {
        switch value {
        case let num as NSNumber:
            if CFBooleanGetTypeID() == CFGetTypeID(num) {
                return num.boolValue
            }
            return num
        case let str as String:
            return str
        case let uuid as UUID:
            return uuid.uuidString
        case let date as Date:
            return ISO8601DateFormatter().string(from: date)
        case let data as Data:
            // For small data blobs, try plist deserialization (shortcuts may be plists)
            if data.count < 1_000_000 {
                if let plist = try? PropertyListSerialization.propertyList(
                    from: data, options: [], format: nil
                ) {
                    return ["_plistDecoded": true, "data": sanitizeValueDeep(plist)]
                }
            }
            return ["_base64": data.base64EncodedString(), "_byteCount": data.count]
        case let url as URL:
            return url.absoluteString
        case let arr as NSArray:
            return arr.map { sanitizeValueDeep($0) }
        case let dict as NSDictionary:
            var result: [String: Any] = [:]
            for (key, val) in dict {
                result["\(key)"] = sanitizeValueDeep(val)
            }
            return result
        case let set as NSSet:
            return set.allObjects.map { sanitizeValueDeep($0) }
        case let components as DateComponents:
            return extractDateComponents(components)
        case let nsObj as NSObject:
            // For complex objects, get their class and description
            let className = String(describing: type(of: nsObj))
            let desc = nsObj.description

            // If it's a HomeKit object, try to extract more via runtime
            if className.hasPrefix("HM") {
                var nested: [String: Any] = ["_class": className]
                // Try common HomeKit properties via KVC
                let commonKeys = [
                    "uniqueIdentifier", "name", "value", "targetValue",
                    "characteristic", "service", "accessory",
                    "actionType", "shortcutIdentifier", "workflowIdentifier",
                    "predicate", "actions", "actionSets",
                    "characteristicType", "serviceType",
                    "triggerValue", "localizedDescription"
                ]
                for key in commonKeys {
                    if let val = safeValue(forKey: key, on: nsObj) {
                        nested[key] = sanitizeValueDeep(val)
                    }
                }
                if nested.count > 1 { return nested }
            }

            return ["_class": className, "_description": desc]
        default:
            return "\(value)"
        }
    }

    // MARK: - Reflection

    /// Use Mirror to recursively extract all stored properties from any object.
    /// This captures data from private HMAction subclasses, shortcut-related
    /// properties, and anything else the HomeKit framework stores internally.
    private func reflectObject(_ obj: Any, depth: Int = 0) -> [String: Any] {
        guard depth < 4 else { return ["_truncated": "max reflection depth reached"] }

        var result: [String: Any] = [:]
        let mirror = Mirror(reflecting: obj)

        for child in mirror.children {
            guard let label = child.label else { continue }
            // Skip internal/private prefixed properties that won't serialize
            let cleanLabel = label.hasPrefix("_") ? String(label.dropFirst()) : label
            result[cleanLabel] = reflectValue(child.value, depth: depth)
        }

        // Also walk superclass mirrors
        var superMirror = mirror.superclassMirror
        while let sm = superMirror {
            for child in sm.children {
                guard let label = child.label else { continue }
                let cleanLabel = label.hasPrefix("_") ? String(label.dropFirst()) : label
                if result[cleanLabel] == nil {
                    result[cleanLabel] = reflectValue(child.value, depth: depth)
                }
            }
            superMirror = sm.superclassMirror
        }

        return result
    }

    /// Convert a reflected value to a JSON-safe type.
    private func reflectValue(_ value: Any, depth: Int) -> Any {
        // Handle optionals
        let mirror = Mirror(reflecting: value)
        if mirror.displayStyle == .optional {
            if mirror.children.isEmpty {
                return NSNull()
            }
            return reflectValue(mirror.children.first!.value, depth: depth)
        }

        // Known safe types
        switch value {
        case let num as NSNumber:
            if CFBooleanGetTypeID() == CFGetTypeID(num) {
                return num.boolValue
            }
            return num
        case let str as String:
            return str
        case let uuid as UUID:
            return uuid.uuidString
        case let date as Date:
            return ISO8601DateFormatter().string(from: date)
        case let data as Data:
            return data.base64EncodedString()
        case let url as URL:
            return url.absoluteString
        case let arr as [Any]:
            return arr.map { reflectValue($0, depth: depth + 1) }
        case let dict as [String: Any]:
            return dict.mapValues { reflectValue($0, depth: depth + 1) }
        case let components as DateComponents:
            return extractDateComponents(components)
        default:
            // For complex objects, try reflecting deeper
            let childMirror = Mirror(reflecting: value)
            if !childMirror.children.isEmpty && depth < 3 {
                var nested = reflectObject(value, depth: depth + 1)
                nested["_class"] = String(describing: type(of: value))
                return nested
            }
            // Fall back to string description
            return "\(value)"
        }
    }

    // MARK: - Helpers

    private func extractDateComponents(_ components: DateComponents) -> [String: Any] {
        var dict: [String: Any] = [:]
        if let era = components.era { dict["era"] = era }
        if let year = components.year { dict["year"] = year }
        if let month = components.month { dict["month"] = month }
        if let day = components.day { dict["day"] = day }
        if let hour = components.hour { dict["hour"] = hour }
        if let minute = components.minute { dict["minute"] = minute }
        if let second = components.second { dict["second"] = second }
        if let weekday = components.weekday { dict["weekday"] = weekday }
        if let weekOfMonth = components.weekOfMonth { dict["weekOfMonth"] = weekOfMonth }
        if let weekOfYear = components.weekOfYear { dict["weekOfYear"] = weekOfYear }
        if let nanosecond = components.nanosecond { dict["nanosecond"] = nanosecond }
        return dict
    }

    /// Converts any value into a JSON-safe type.
    private func sanitizeValue(_ value: Any) -> Any {
        switch value {
        case let num as NSNumber:
            // Distinguish booleans from other numbers
            if CFBooleanGetTypeID() == CFGetTypeID(num) {
                return num.boolValue
            }
            return num
        case let str as String:
            return str
        case let data as Data:
            return data.base64EncodedString()
        case let arr as [Any]:
            return arr.map { sanitizeValue($0) }
        case let dict as [String: Any]:
            return dict.mapValues { sanitizeValue($0) }
        default:
            return "\(value)"
        }
    }

    private func saveToDocuments(data: Data, filename: String) -> String {
        let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let fileURL = documentsURL.appendingPathComponent(filename)

        do {
            try data.write(to: fileURL, options: .atomic)
        } catch {
            DispatchQueue.main.async {
                self.statusMessage = "Failed to write file: \(error.localizedDescription)"
            }
        }

        return fileURL.path
    }
}
