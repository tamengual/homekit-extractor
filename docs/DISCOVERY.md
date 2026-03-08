# How We Found the homed Database

The story of extracting 206 HomeKit automations from Apple's internal daemon database -- including the 66 that Apple's own API refused to show us.

---

## The Setup

There were roughly 200 HomeKit automations controlling a house full of smart devices -- lights, blinds, locks, buttons, sensors, motion detectors -- spread across dozens of rooms. The goal: migrate everything to Home Assistant.

No export button exists in the Home app. No "download my automations" feature. Apple provides `HMHomeManager`, a Swift API that lets third-party apps read HomeKit data, but nothing that produces a portable file you could take to another platform. So the first step was building a tool to get the data out.

This turned into a collaboration between two Claude Code sessions running on different machines. One ran on a MacBook, with direct access to HomeKit and macOS internals. The other ran on a Windows PC, doing the data analysis and Home Assistant conversion work. The human coordinated between them, shuttling files through Google Drive and OneDrive, providing the domain knowledge about which physical button was in which room and what each automation was supposed to do.

The MacBook session built the extraction tools. The Windows session consumed what they produced. This document is about what happened on the Mac side -- the detective work of getting the data out.

---

## Act 1: The Catalyst App (What the API Gives You)

The first approach was straightforward. Build a Mac Catalyst app in SwiftUI that uses Apple's official `HMHomeManager` API. The app would enumerate every home, room, accessory, scene, and automation, then serialize the whole thing to JSON.

The app worked. It produced a 207-automation export with full trigger conditions, action lists, accessory metadata, room assignments, and characteristic details. For most automations, this was complete -- you could see that "when the front door lock changes state, turn on the hallway light" was an event trigger on characteristic type `Lock Current State` firing an action set that writes `true` to the `Power State` characteristic of the hallway light accessory.

Then there were the other ones.

Of the 207 automations, 96 contained at least one `HMShortcutAction`. These were automations the user had, at some point, tapped "Convert to Shortcut" on in the Home app. That conversion unlocks powerful features -- if/then conditionals, multi-branch menus, the ability to check the state of one device before acting on another. It also makes the automation completely opaque to the API.

`HMShortcutAction` exposes exactly one property: a name. That is all. No workflow definition, no list of actions, no conditionals, no device references. Apple stores the actual Shortcuts workflow on the Home Hub (an Apple TV or HomePod) and syncs it through CloudKit. The API on your Mac gets a string label and nothing else.

After deduplication and further analysis, 66 of these automations were purely shortcut-based -- their entire logic was hidden behind that opaque wall. These were not trivial automations either. They were the *complex* ones: the toggle patterns (if the light is on, turn it off, otherwise turn it on), the multi-device conditional sequences, the ones that checked whether "party mode" was active before deciding what to do. Losing them meant losing the most important automations in the house.

---

## Act 2: The Dead Ends

What followed was a systematic search through every database, cache, and data store on the Mac that might contain HomeKit shortcut data. Each one looked promising. Each one was a dead end.

### Dead End 1: Controller for HomeKit

Controller for HomeKit is a popular third-party app for managing HomeKit setups. It has its own local databases. Maybe it cached the shortcut data?

The app's container at `~/Library/Containers/com.janandre.HomeKitTimers/` contained several promising files:

- `homeMeta_*.json` -- UI metadata only: automation names, sort order, favorites. No workflow data.
- `homeIds_*.json` -- Maps internal IDs to HomeKit UUIDs. Useful for cross-referencing, useless for shortcut content.
- `default.realm` -- A 14MB Realm database with `HMTrigger` and `HMActionSet` references.

The Realm database was the most promising. At 14MB, it clearly had substance. But opening it revealed the same limitation: Controller for HomeKit uses the same `HMHomeManager` API as everyone else. Its database was a UI cache of API results. The shortcut actions were stored as references with names and nothing more. Same wall, different app.

### Dead End 2: Apple Shortcuts Databases

HomeKit "Convert to Shortcut" automations become Shortcuts workflows. The Shortcuts app has its own data storage. Maybe the workflows lived there?

The `shortcuts` CLI tool listed 46 shortcuts on the system, some with HomeKit-related names. But the CLI has no export functionality -- it can run shortcuts and list them, but cannot dump their workflow definitions.

The Shortcuts app's SQLite databases in its group containers were even less helpful: empty migration databases with schema but no content. The actual Shortcuts data for HomeKit automations is not stored in the Shortcuts app's local database at all -- it lives on the Home Hub and syncs separately.

### Dead End 3: HomeKit Tomb (com.apple.sbd)

`~/Library/Application Support/com.apple.sbd/` looked interesting -- `sbd` is Apple's secure backup daemon, and it contained HomeKit-related data. But this turned out to be encrypted keychain synchronization data. The files are protected by the Secure Enclave and macOS keychain encryption. No amount of SQLite inspection or plist parsing would crack them open, nor should it -- this is security infrastructure doing its job.

### Dead End 4: Encrypted Controller Backups

Controller for HomeKit can create `.backup` files of your HomeKit configuration. These are AES-encrypted archives in an undocumented format. Without the encryption key derivation method and archive structure documentation, these were opaque binaries. Another wall.

---

## Act 3: The Breakthrough

After exhausting the obvious candidates, the approach shifted from "find a database that has the data" to "find the process that has the data and see where it keeps it."

HomeKit on macOS is managed by a daemon called `homed`. It runs continuously, communicating with Home Hubs, processing automation events, and maintaining the local state of the entire HomeKit configuration. If any process on the Mac had the complete shortcut data, it would be this one.

```bash
# Confirm homed is running
launchctl list | grep home
# Output: com.apple.homed, PID 1147
```

The daemon was alive. The next question: what files does it have open?

```bash
lsof -p $(pgrep homed)
```

The output listed dozens of open file descriptors. Most were system libraries, Mach ports, and network sockets. But buried in the list were several SQLite databases under a directory nobody had thought to check:

```
~/Library/HomeKit/core.sqlite
~/Library/HomeKit/core-cloudkit.sqlite
~/Library/HomeKit/core-cloudkit-shared.sqlite
~/Library/HomeKit/core-local.sqlite
~/Library/HomeKit/datastore3.sqlite
```

`~/Library/HomeKit/` -- not `~/Library/Application Support/`, not a container, not a group container. A dedicated directory under `~/Library/` that does not appear in any Apple documentation about HomeKit data locations.

The main file, `core.sqlite`, was 45MB. For context, the entire API export JSON was about 600KB. Forty-five megabytes of SQLite meant this was not a cache. This was the real thing.

There was one catch: the directory is protected by macOS TCC (Transparency, Consent, and Control). Terminal needs Full Disk Access to read it. A quick trip to System Settings, Privacy & Security, Full Disk Access, add Terminal -- and the database was readable.

```bash
sqlite3 ~/Library/HomeKit/core.sqlite ".tables"
```

The output was a wall of CoreData-generated table names. `ZMKFTRIGGER`. `ZMKFACTIONSET`. `ZMKFACTION`. `ZMKFACCESSORY`. `ZMKFSERVICE`. `ZMKFCHARACTERISTIC`. `ZMKFROOM`. `ZMKFEVENT`. And the magic one: `Z_PRIMARYKEY`, which maps CoreData entity type IDs to human-readable names.

```sql
SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY WHERE Z_NAME LIKE '%Action%' OR Z_NAME LIKE '%Trigger%';
```

| Z_ENT | Z_NAME |
|-------|--------|
| 36 | MKFCharacteristicWriteAction |
| 40 | MKFShortcutAction |
| 136 | MKFEventTrigger |
| 137 | MKFTimerTrigger |

Entity type 40: `MKFShortcutAction`. And in the `ZMKFACTION` table, rows with `Z_ENT=40` had a column called `ZDATA` -- containing binary blobs ranging from 3KB to 15KB each.

Those blobs were not empty placeholders. They were not UUID references to data on the Home Hub. They were the actual shortcut workflow definitions, right there in the local database.

---

## Act 4: Peeling Back the Layers

Having the data and being able to read it were two different problems. The `ZDATA` blobs in `MKFShortcutAction` rows turned out to be encoded through three nested layers, each using a different serialization format.

### Layer 1: Binary Property List to WFWorkflowActions

The outermost layer was a binary plist -- Apple's compact binary serialization format. Python's `plistlib.loads()` handled it directly:

```python
import plistlib

plist = plistlib.loads(zdata_blob)
workflow_actions = plist['WFWorkflowActions']
```

The `WFWorkflowActions` key contained an array of workflow steps in the same format used by Apple's Shortcuts app. Each step had a `WFWorkflowActionIdentifier` (like `is.workflow.actions.conditional` or `is.workflow.actions.homeaccessory`) and a `WFWorkflowActionParameters` dictionary.

The conditional and menu actions decoded cleanly at this layer. A conditional block used `WFControlFlowMode` values of 0, 1, and 2 for if, else, and end respectively. Menu actions used the same pattern with start, case, and end. The condition type, comparison value, and input references were all present in the parameters.

But the `homeaccessory` actions -- the ones that actually control devices -- had another layer of encoding inside them.

### Layer 2: Protobuf (No Schema Available)

Inside each `homeaccessory` workflow step, the device control data was stored under `WFHomeTriggerActionSets` > `WFHFTriggerActionSetsBuilderParameterStateActionSets` > `HMActionSetSerializedData`. The value was a raw bytes blob, and it was not a plist.

The byte pattern gave it away: leading varints, field tags with wire type encoding, length-delimited byte strings. This was Protocol Buffers (protobuf), Google's binary serialization format that Apple uses internally in several frameworks.

There was no `.proto` schema file available. The format had to be decoded by inspection -- reading the raw field numbers, wire types, and values, then inferring the structure from the content:

```python
def parse_protobuf(data, depth=0):
    """Raw protobuf field parser -- no schema needed."""
    fields = {}
    i = 0
    while i < len(data):
        tag, i = decode_varint(data, i)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:      # varint
            val, i = decode_varint(data, i)
            fields.setdefault(field_number, []).append(('varint', val))
        elif wire_type == 2:    # length-delimited
            length, i = decode_varint(data, i)
            val = data[i:i+length]
            fields.setdefault(field_number, []).append(('bytes', val))
            i += length
        # ... fixed32, fixed64 ...
    return fields
```

Through iterative decoding, the protobuf structure revealed itself:

- **Field 1:** Action set UUID as an ASCII string
- **Field 2:** Repeated write actions (nested messages), each containing:
  - Sub-field 1: Enabled flag (varint, 0 or 1)
  - Sub-field 2: Target value data (another blob, with yet more encoding inside)
  - Sub-field 3: Service/characteristic/accessory reference chain (nested UUIDs)
- **Field 4:** Scene model UUID (16 raw bytes)
- **Field 5:** Home model UUID (16 raw bytes)

The 16-byte raw UUIDs in fields 4 and 5 could be converted directly with Python's `uuid.UUID(bytes=...)`. The reference chains in field 3 contained nested protobuf messages with their own 16-byte UUID payloads, forming a hierarchy of accessory, service, and characteristic references.

But the target values in field 2 were yet another format.

### Layer 3: NSKeyedArchiver Binary Plists

The target value data inside each protobuf write action contained an NSKeyedArchiver-encoded binary plist. NSKeyedArchiver is Apple's object graph serialization format -- it wraps Objective-C/Swift objects into a keyed archive that preserves type information and object references.

The trick was finding where the plist started within the protobuf bytes field. The `bplist00` magic bytes marked the boundary:

```python
def extract_target_value_from_nsarchiver(data):
    """Find the bplist00 magic, parse from there, extract actual value."""
    bp_idx = data.find(b'bplist')
    if bp_idx < 0:
        return None
    plist = plistlib.loads(data[bp_idx:])
    if isinstance(plist, dict) and '$objects' in plist:
        for obj in plist['$objects']:
            if obj != '$null' and not isinstance(obj, dict):
                return obj  # The actual value: True, False, 100, "some string"
    return None
```

The `$objects` array in an NSKeyedArchiver plist contains the serialized object graph. For simple target values (booleans for on/off, integers for brightness percentages, floats for color temperatures), the actual value sat in that array as a plain scalar, sandwiched between the `$null` sentinel and various class metadata dictionaries.

Three layers deep -- binary plist wrapping protobuf wrapping NSKeyedArchiver binary plist -- and the actual "turn this light to 75% brightness" value finally emerged as the integer `75`.

---

## Act 5: The UUID Mystery

With the decoding pipeline working, the next challenge was identification: the protobuf data contained UUIDs referencing accessories, services, and characteristics, but which physical devices did they refer to?

The natural assumption was that these UUIDs would match something in the database. The `ZMKFACCESSORY` table has a `ZMODELID` column. The `ZMKFSERVICE` table has `ZUNIQUEIDENTIFIER`. The API export has `uniqueIdentifier` fields on every object. Surely the protobuf UUIDs would match one of these.

They matched none of them.

A systematic search was conducted: every 16-byte UUID extracted from every protobuf blob across all 66 shortcut automations was compared against every UUID column in the homed database (`ZMODELID`, `ZUNIQUEIDENTIFIER`, `ZFAILEDMODELID`) and every `uniqueIdentifier` in the HMHomeManager API export. Over 1,419 lookups. Zero matches.

The protobuf UUIDs form a completely isolated namespace. They are internal identifiers used by the Home Hub's execution engine, with no mapping to any externally accessible identifier. They are not the same as the UUIDs that `HMHomeManager` assigns to objects, they are not the same as the model IDs that CoreData uses, and they are not the same as the UUIDs that CloudKit uses for sync.

This was a significant setback for automation conversion. Without UUID resolution, there was no reliable way to determine which physical device a shortcut workflow step was controlling. The protobuf bytes told you "set characteristic UUID `A1B2C3D4-...` on service UUID `E5F6G7H8-...` to value `true`" but could not tell you that this meant "turn on the kitchen light."

The fallback was name-based matching. The homed database's `ZMKFACCESSORY` table contains `ZCONFIGUREDNAME` (the user-visible device name), and the API export contains `name` fields on every accessory. By matching automation names and cross-referencing action patterns, it was possible to identify most devices -- but it required the merge script to correlate data from both export methods rather than relying on protobuf UUIDs alone.

This also revealed a second UUID mismatch: the HMHomeManager API export uses completely different UUIDs from the homed database itself. The `uniqueIdentifier` values from the API have zero overlap with `ZUNIQUEIDENTIFIER` values in the database. Matching between the API export and the homed export must also be done by name, not by UUID.

---

## Act 6: The Other Gotchas

Beyond the major encoding and UUID challenges, several smaller traps emerged during extraction:

**The ZACCESSORY1 Column.** Every `MKFCharacteristicWriteAction` row (Z_ENT=36) has a column called `ZACCESSORY` and a column called `ZACCESSORY1`. The obvious one, `ZACCESSORY`, is NULL for all 1,161 rows. The correct foreign key to `ZMKFACCESSORY` lives in `ZACCESSORY1`. This is almost certainly a CoreData schema migration artifact -- at some point Apple renamed or moved the relationship, and CoreData created a new column rather than reusing the old one.

**CoreData Junction Table Naming.** The many-to-many relationship between triggers and action sets uses a junction table called `Z_41TRIGGERS_` with columns named `Z_41ACTIONSETS_` and `Z_135TRIGGERS_`. These numbers are CoreData's internal relationship identifiers, derived from `Z_ENT` values but not always matching them directly. Without inspecting `PRAGMA table_info()`, you would not guess the column names.

**CoreData Timestamps.** All `ZWRITERTIMESTAMP` and `ZMOSTRECENTFIREDATE` values use Apple's CoreData epoch: seconds since January 1, 2001 at 00:00:00 UTC. To convert to Unix time, add 978307200.

---

## The Result

The final extraction pipeline -- combining the Catalyst app's API export with the homed database extraction -- produced a complete picture of all 206 automations:

| Category | Count |
|----------|-------|
| Total automations extracted | 206 |
| Shortcut automations (fully decoded) | 66 |
| Characteristic write actions | 1,211 |
| Decoded workflow steps | 336 |

The 66 previously opaque shortcut automations were now fully readable JSON structures, complete with conditional logic, device actions, target values, and scene references. The toggle patterns were visible (if power state is on, turn off, else turn on). The conditional branches were traceable (if it is after sunset, set brightness to 40%, else set to 100%). The multi-device sequences were laid out step by step.

The Windows-side Claude Code session then took this data and converted 177 automations into Home Assistant YAML, producing 1,964 service calls across all automations. The entity mapping used fuzzy name matching between 272 HomeKit accessories and the Home Assistant entity registry, because -- as the UUID mystery demonstrated -- name matching was the only reliable correlation method available.

---

## What Made This Work

Three things made this discovery possible:

1. **`lsof` on the right process.** The entire breakthrough came from asking the operating system which files the `homed` daemon had open, rather than trying to guess where Apple stores HomeKit data. The database was in `~/Library/HomeKit/`, a location not documented in any Apple developer resource, not mentioned in any Stack Overflow answer, and not inside any app container.

2. **Python stdlib was enough.** The three-layer decoding pipeline -- binary plist, protobuf, NSKeyedArchiver -- used only `plistlib`, `struct`-level byte manipulation, and `sqlite3`. No protobuf compiler, no third-party libraries, no reverse engineering tools. Apple's choice of standard formats (even if deeply nested) meant that each layer could be peeled back with basic tools.

3. **Two machines, two sessions, one human.** The MacBook session had access to HomeKit but not to Home Assistant. The Windows session had access to Home Assistant but not to HomeKit. The human moved files between them and provided the context that neither machine had alone -- which room is the "upstairs hallway," which button is the one by the bed, which automations are actually important and which are leftovers from experiments. The architecture was not planned; it emerged from the constraint that HomeKit runs on Apple hardware and Home Assistant ran on a separate machine.

---

## For Anyone Attempting This

If you find yourself needing to extract HomeKit shortcut automation data:

1. Grant Full Disk Access to Terminal (System Settings > Privacy & Security).
2. Copy `~/Library/HomeKit/core.sqlite` somewhere safe (the original is read-only and TCC-protected, but make a copy anyway).
3. Open it with `sqlite3` and start with `SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY` to orient yourself.
4. The `ZMKFACTION` table with `Z_ENT=40` contains your shortcut workflows in the `ZDATA` column.
5. Decode through the three layers: `plistlib.loads()` for the outer plist, raw protobuf parsing for `HMActionSetSerializedData`, and `plistlib.loads()` again (scanning for `bplist00`) for target values.
6. Do not expect the UUIDs inside the protobuf to match anything in the database or the API. They will not. Match by name.

The complete extraction script is in [`scripts/homed_extract.py`](../scripts/homed_extract.py). It is pure Python stdlib, runs in seconds, and produces a JSON file with everything.

---

*This document describes work done in March 2026. Apple could change the homed database schema, location, or access controls in any future macOS update. The extraction method described here was tested on macOS Sequoia.*
