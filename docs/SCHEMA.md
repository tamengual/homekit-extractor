# homed CoreData SQLite Database Schema Reference

> Technical reference for Apple's `homed` daemon database (`core.sqlite`),
> which stores the complete HomeKit configuration including automations,
> scenes, devices, and -- critically -- the full Shortcuts workflow definitions
> that are invisible to the public `HMHomeManager` API.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Entity Type Table (Z_PRIMARYKEY)](#2-entity-type-table-z_primarykey)
3. [Key Tables](#3-key-tables)
4. [Junction Tables](#4-junction-tables)
5. [Relationship Chain](#5-relationship-chain)
6. [Data Encoding](#6-data-encoding)
7. [Critical Gotchas](#7-critical-gotchas)
8. [Useful SQL Queries](#8-useful-sql-queries)

---

## 1. Overview

### What is it?

`core.sqlite` is a CoreData-managed SQLite database used by Apple's `homed`
daemon (launch service `com.apple.homed`) to persist the full HomeKit object
graph. It contains every automation, scene, accessory, room, service,
characteristic, and -- most importantly -- the binary Shortcuts workflow data
embedded inside `HMShortcutAction` automations.

### Where does it live?

```
~/Library/HomeKit/core.sqlite
```

Other databases in the same directory (`core-cloudkit.sqlite`,
`core-cloudkit-shared.sqlite`, `core-local.sqlite`, `datastore3.sqlite`) are
for CloudKit sync metadata and are not needed for extraction.

### Access requirements

| Requirement | Details |
|---|---|
| macOS TCC protection | The `~/Library/HomeKit/` directory is protected by Transparency, Consent, and Control |
| Full Disk Access | Grant via **System Settings > Privacy & Security > Full Disk Access** for Terminal (or your app) |
| Read-only access | Always open with `?mode=ro` to avoid corrupting the live database |

```python
# Correct way to open the database (read-only)
conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
```

### CoreData conventions

Because this is a CoreData store, all tables and columns follow Apple's
auto-generated naming scheme:

- Table names: `Z` + uppercase entity name (e.g., `ZMKFTRIGGER`)
- Primary key: `Z_PK` (integer, auto-increment)
- Entity type discriminator: `Z_ENT` (references `Z_PRIMARYKEY.Z_ENT`)
- Optimistic locking counter: `Z_OPT`
- Foreign keys: `Z` + uppercase relationship name (e.g., `ZHOME`, `ZACTIONSET`)
- Metadata tables: `Z_PRIMARYKEY`, `Z_METADATA`, `Z_MODELCACHE`

---

## 2. Entity Type Table (`Z_PRIMARYKEY`)

The `Z_PRIMARYKEY` table maps integer entity type IDs (`Z_ENT`) to entity
class names. Every row in every `ZMKF*` table has a `Z_ENT` column that
references this table, enabling CoreData's single-table inheritance pattern.

### Action Entity Types

| Z_ENT | Z_NAME | Description |
|-------|--------|-------------|
| 36 | MKFCharacteristicWriteAction | Set a characteristic value (on/off, brightness, color, etc.) |
| 37 | MKFMatterCommandAction | Matter protocol commands |
| 38 | MKFMediaPlaybackAction | Media playback control (HomePod, Apple TV) |
| 39 | MKFNaturalLightingAction | Adaptive lighting configuration |
| 40 | MKFShortcutAction | **Contains full Shortcuts workflow in ZDATA column** |

### Event Entity Types

| Z_ENT | Z_NAME | Description |
|-------|--------|-------------|
| 88 | MKFCalendarEvent | Time/date-based trigger event |
| 90 | MKFCharacteristicRangeEvent | Trigger when characteristic is within a range |
| 91 | MKFCharacteristicValueEvent | Trigger when characteristic equals a value |
| 92 | MKFDurationEvent | Duration-based event (e.g., "after 5 minutes") |
| 93 | MKFLocationEvent | Geofence-based trigger (arrive/leave area) |
| 96 | MKFPresenceEvent | People presence trigger (arrive/leave home) |
| 97 | MKFSignificantTimeEvent | Sunrise/sunset trigger with optional offset |

### Trigger Entity Types

| Z_ENT | Z_NAME | Description |
|-------|--------|-------------|
| 136 | MKFEventTrigger | Event-based automation trigger (sensor, time of day, etc.) |
| 137 | MKFTimerTrigger | Timer/schedule-based automation trigger |

> **Note:** The Z_ENT values above were observed on a specific macOS installation.
> They are assigned by CoreData during model creation and _may_ differ across
> macOS versions or HomeKit schema migrations. Always verify with:
> ```sql
> SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY ORDER BY Z_ENT;
> ```

---

## 3. Key Tables

### `ZMKFTRIGGER` -- Automations

Each row represents one automation (what the Home app shows as an automation card).

| Column | Type | Description |
|--------|------|-------------|
| `Z_PK` | INTEGER | Primary key |
| `Z_ENT` | INTEGER | 136 = EventTrigger, 137 = TimerTrigger |
| `Z_OPT` | INTEGER | CoreData optimistic lock counter |
| `ZACTIVE` | INTEGER | 1 = enabled, 0 = disabled |
| `ZAUTODELETE` | INTEGER | Auto-deletion flag |
| `ZHOME` | INTEGER | FK to home entity |
| `ZOWNER` | INTEGER | FK to owner entity |
| `ZEXECUTEONCE` | INTEGER | Run-once flag |
| `ZRECURRENCEDAYS` | INTEGER | Bitmask of recurrence days |
| `ZRECURRENCEDAYS1` | INTEGER | Additional recurrence days field |
| `ZSIGNIFICANTEVENTOFFSETSECONDS` | FLOAT | Offset from sunrise/sunset in seconds |
| `ZMOSTRECENTFIREDATE` | TIMESTAMP | Last execution time (CoreData epoch) |
| `ZWRITERTIMESTAMP` | TIMESTAMP | Last modification time (CoreData epoch) |
| `ZFIREDATE` | TIMESTAMP | Scheduled fire date for timer triggers |
| `ZCONFIGUREDNAME` | VARCHAR | **User-visible automation name** |
| `ZNAME` | VARCHAR | Internal name |
| `ZSIGNIFICANTEVENT` | VARCHAR | `"sunrise"` or `"sunset"` for significant time triggers |
| `ZMODELID` | VARCHAR | CoreData model UUID |
| `ZEVALUATIONCONDITION` | BLOB | Binary plist NSPredicate ("any" vs "all" conditions) |
| `ZFIREDATETIMEZONE` | BLOB | Timezone data for fire date |
| `ZFIREREPEATINTERVAL` | BLOB | Repeat interval configuration |
| `ZRECURRENCES` | BLOB | Recurrence rule data |
| `ZRECURRENCES1` | BLOB | Additional recurrence data |
| `ZSIGNIFICANTEVENTOFFSET` | BLOB | Structured offset from significant event |

---

### `ZMKFACTIONSET` -- Scenes / Action Groups

Each automation executes one or more action sets. Scenes are also stored here.

| Column | Type | Description |
|--------|------|-------------|
| `Z_PK` | INTEGER | Primary key |
| `Z_ENT` | INTEGER | Entity type |
| `Z_OPT` | INTEGER | CoreData optimistic lock counter |
| `ZHOME` | INTEGER | FK to home entity |
| `ZTYPE` | VARCHAR | Action set type (e.g., `com.apple.HomeKit.actionSet.type.trigger`) |
| `ZWRITERTIMESTAMP` | TIMESTAMP | Last modification time (CoreData epoch) |
| `ZCONFIGUREDNAME` | VARCHAR | User-visible scene name |
| `ZNAME` | VARCHAR | Internal name |
| `ZMODELID` | VARCHAR | CoreData model UUID |
| `ZUNIQUEIDENTIFIER` | VARCHAR | Unique identifier |

---

### `ZMKFACTION` -- Individual Actions

Each action set contains one or more actions. This is a **polymorphic table** --
the `Z_ENT` column determines which columns are populated.

| Column | Type | Description |
|--------|------|-------------|
| `Z_PK` | INTEGER | Primary key |
| `Z_ENT` | INTEGER | Action type (36, 37, 38, 39, or 40 -- see entity types) |
| `Z_OPT` | INTEGER | CoreData optimistic lock counter |
| `ZACTIONSET` | INTEGER | FK to `ZMKFACTIONSET.Z_PK` |
| `ZTARGETSLEEPWAKESTATE` | INTEGER | Sleep/wake state target |
| `ZACCESSORY` | INTEGER | FK to accessory -- **ALWAYS NULL for Z_ENT=36, see Gotcha 1** |
| `ZCHARACTERISTICID` | INTEGER | FK to `ZMKFCHARACTERISTIC.Z_PK` |
| `ZACCESSORY1` | INTEGER | **Correct FK to `ZMKFACCESSORY.Z_PK` for CharacteristicWriteActions** |
| `ZSERVICE` | INTEGER | FK to `ZMKFSERVICE.Z_PK` |
| `ZSTATE` | INTEGER | Target state |
| `ZNATURALLIGHTINGENABLEDFIELD` | INTEGER | Adaptive lighting enabled flag |
| `ZACCESSORY2` | INTEGER | Additional accessory reference |
| `ZWRITERTIMESTAMP` | TIMESTAMP | Last modification time (CoreData epoch) |
| `ZVOLUME` | FLOAT | Volume level for media actions |
| `ZMODELID` | VARCHAR | CoreData model UUID |
| `ZLIGHTPROFILEUUID` | VARCHAR | Light profile reference |
| `ZTARGETVALUE` | BLOB | Binary plist -- target value for CharacteristicWriteAction (Z_ENT=36) |
| `ZENCODEDPLAYBACKARCHIVE` | BLOB | Encoded media playback data |
| `ZSERVICEUUIDS` | BLOB | Service UUID references |
| `ZDATA` | BLOB | **For ShortcutAction (Z_ENT=40): binary plist with full workflow** |
| `ZCOMMANDS` | BLOB | Matter command data |
| `ZENFORCEEXECUTIONORDER` | BLOB | Execution ordering data |

#### Columns populated by action type

| Column | Z_ENT=36 (CharWrite) | Z_ENT=40 (Shortcut) |
|--------|-----------------------|----------------------|
| `ZACCESSORY` | always NULL | -- |
| `ZACCESSORY1` | **valid FK** | -- |
| `ZCHARACTERISTICID` | valid FK | -- |
| `ZSERVICE` | valid FK | -- |
| `ZTARGETVALUE` | binary plist | -- |
| `ZDATA` | -- | **binary plist with WFWorkflowActions** |

---

### `ZMKFEVENT` -- Trigger Conditions

Each trigger has one or more events that define when it fires.

| Column | Type | Description |
|--------|------|-------------|
| `Z_PK` | INTEGER | Primary key |
| `Z_ENT` | INTEGER | Event type (88, 90, 91, 92, 93, 96, or 97) |
| `Z_OPT` | INTEGER | CoreData optimistic lock counter |
| `ZENDEVENT` | INTEGER | FK to end event (for duration-based events) |
| `ZTRIGGER` | INTEGER | FK to `ZMKFTRIGGER.Z_PK` |
| `ZCHARACTERISTICID` | INTEGER | FK to `ZMKFCHARACTERISTIC.Z_PK` |
| `ZSERVICE` | INTEGER | FK to `ZMKFSERVICE.Z_PK` |
| `ZUSER` | INTEGER | FK to user entity (for presence events) |
| `ZACTIVATION` | INTEGER | Activation state |
| `ZOFFSETSECONDS` | FLOAT | Offset in seconds from significant event |
| `ZWRITERTIMESTAMP` | TIMESTAMP | Last modification time (CoreData epoch) |
| `ZDURATION` | FLOAT | Duration value |
| `ZPRESENCETYPE` | INTEGER | Presence type flag (arrive/leave) |
| `ZSIGNIFICANTEVENT` | VARCHAR | `"sunrise"` or `"sunset"` |
| `ZMODELID` | VARCHAR | CoreData model UUID |
| `ZFIREDATECOMPONENTS` | BLOB | Date components for calendar events |
| `ZMAX` | BLOB | Maximum range value (for range events) |
| `ZMIN` | BLOB | Minimum range value (for range events) |
| `ZEVENTVALUE` | BLOB | Binary plist -- the trigger comparison value |
| `ZREGION` | BLOB | Geofence region data (for location events) |
| `ZOFFSET` | BLOB | Structured offset data |
| `ZMATTERPATH` | BLOB | Matter protocol path |
| `ZEVENTVALUE1` | BLOB | Additional event value |

---

### `ZMKFACCESSORY` -- Devices

Each physical or bridged HomeKit device.

| Column | Type | Description |
|--------|------|-------------|
| `Z_PK` | INTEGER | Primary key |
| `Z_ENT` | INTEGER | Entity type |
| `Z_OPT` | INTEGER | CoreData optimistic lock counter |
| `ZROOM` | INTEGER | FK to `ZMKFROOM.Z_PK` |
| `ZCONFIGUREDNAME` | VARCHAR | **User-assigned device name** |
| `ZNAME` | VARCHAR | Manufacturer default name |
| `ZMANUFACTURER` | VARCHAR | Manufacturer name |
| `ZMODEL` | VARCHAR | Model identifier |
| `ZUNIQUEIDENTIFIER` | VARCHAR | HomeKit unique identifier |
| `ZMODELID` | VARCHAR | CoreData model UUID |
| `ZWRITERTIMESTAMP` | TIMESTAMP | Last modification time (CoreData epoch) |

> Additional columns exist for firmware versions, reachability, category,
> and bridged accessory relationships. Use `PRAGMA table_info(ZMKFACCESSORY)`
> for the full list.

---

### `ZMKFSERVICE` -- Services

Each accessory exposes one or more services (e.g., Lightbulb, Switch, Thermostat).

| Column | Type | Description |
|--------|------|-------------|
| `Z_PK` | INTEGER | Primary key |
| `Z_ENT` | INTEGER | Entity type |
| `Z_OPT` | INTEGER | CoreData optimistic lock counter |
| `ZACCESSORY` | INTEGER | FK to `ZMKFACCESSORY.Z_PK` |
| `ZNAME` | VARCHAR | Service type name (e.g., "Lightbulb") |
| `ZEXPECTEDCONFIGUREDNAME` | VARCHAR | Expected user-facing name |
| `ZMODELID` | VARCHAR | CoreData model UUID |
| `ZUNIQUEIDENTIFIER` | VARCHAR | Service unique identifier |
| `ZWRITERTIMESTAMP` | TIMESTAMP | Last modification time (CoreData epoch) |

---

### `ZMKFCHARACTERISTIC` -- Characteristics

Each service has one or more characteristics (e.g., On/Off, Brightness, Hue).

| Column | Type | Description |
|--------|------|-------------|
| `Z_PK` | INTEGER | Primary key |
| `Z_ENT` | INTEGER | Entity type |
| `Z_OPT` | INTEGER | CoreData optimistic lock counter |
| `ZSERVICE` | INTEGER | FK to `ZMKFSERVICE.Z_PK` |
| `ZTYPE` | BLOB | 16-byte UUID identifying the characteristic type |
| `ZMANUFACTURERDESCRIPTION` | VARCHAR | Human-readable description (e.g., "Brightness") |
| `ZFORMAT` | VARCHAR | Value format (`"bool"`, `"int"`, `"float"`, `"string"`, etc.) |
| `ZMODELID` | VARCHAR | CoreData model UUID |
| `ZWRITERTIMESTAMP` | TIMESTAMP | Last modification time (CoreData epoch) |

---

### `ZMKFROOM` -- Rooms

Room assignments for accessories.

| Column | Type | Description |
|--------|------|-------------|
| `Z_PK` | INTEGER | Primary key |
| `Z_ENT` | INTEGER | Entity type |
| `Z_OPT` | INTEGER | CoreData optimistic lock counter |
| `ZHOME` | INTEGER | FK to home entity |
| `ZNAME` | VARCHAR | **Room name** (e.g., "Kitchen", "Living Room") |
| `ZMODELID` | VARCHAR | CoreData model UUID |
| `ZUNIQUEIDENTIFIER` | VARCHAR | Room unique identifier |
| `ZWRITERTIMESTAMP` | TIMESTAMP | Last modification time (CoreData epoch) |

---

## 4. Junction Tables

### `Z_41TRIGGERS_` -- Trigger-to-ActionSet Many-to-Many

Automations (triggers) can execute multiple action sets, and action sets can
belong to multiple triggers. This many-to-many relationship is stored in a
CoreData-generated junction table.

| Column | Type | References |
|--------|------|------------|
| `Z_41ACTIONSETS_` | INTEGER | FK to `ZMKFACTIONSET.Z_PK` |
| `Z_135TRIGGERS_` | INTEGER | FK to `ZMKFTRIGGER.Z_PK` |

### Column naming convention

CoreData auto-generates junction table and column names using internal
relationship IDs from the CoreData model. The pattern is:

```
Table:   Z_{relationship_id_A}{RELATIONSHIP_NAME_B}_
Column:  Z_{relationship_id_A}{RELATIONSHIP_NAME_A}_   (FK to entity A)
Column:  Z_{relationship_id_B}{RELATIONSHIP_NAME_B}_   (FK to entity B)
```

The numbers (41, 135) are CoreData's internal relationship indices from the
managed object model, not the Z_ENT values from `Z_PRIMARYKEY`. They are
stable within a given schema version but may change across CoreData model
migrations.

**Always verify column names at runtime:**
```sql
PRAGMA table_info(Z_41TRIGGERS_);
```

---

## 5. Relationship Chain

```
ZMKFTRIGGER (automation)
 |
 |-- Z_ENT: 136 (EventTrigger) or 137 (TimerTrigger)
 |-- ZCONFIGUREDNAME: user-visible name
 |-- ZEVALUATIONCONDITION: binary plist NSPredicate
 |
 |--- [events] --------> ZMKFEVENT (trigger conditions)
 |    via ZTRIGGER FK         |
 |                            |-- Z_ENT: 88/90/91/92/93/96/97
 |                            |-- ZCHARACTERISTICID ---> ZMKFCHARACTERISTIC
 |                            +-- ZSERVICE -----------> ZMKFSERVICE
 |
 +--- [action sets] ---> Z_41TRIGGERS_ (junction table)
      via Z_135TRIGGERS_      |
                              +-- Z_41ACTIONSETS_ ---> ZMKFACTIONSET (scene/action group)
                                                        |
                                                        |-- ZCONFIGUREDNAME: scene name
                                                        |-- ZTYPE: action set type
                                                        |
                                                        +--- [actions] ---> ZMKFACTION
                                                             via ZACTIONSET FK    |
                                                                                  |-- Z_ENT=36: CharacteristicWrite
                                                                                  |   |-- ZACCESSORY1 --> ZMKFACCESSORY
                                                                                  |   |                      +-- ZROOM --> ZMKFROOM
                                                                                  |   |-- ZSERVICE ----> ZMKFSERVICE
                                                                                  |   |-- ZCHARACTERISTICID -> ZMKFCHARACTERISTIC
                                                                                  |   +-- ZTARGETVALUE (binary plist)
                                                                                  |
                                                                                  +-- Z_ENT=40: ShortcutAction
                                                                                      +-- ZDATA (binary plist)
                                                                                          +-- WFWorkflowActions[]
                                                                                              +-- homeaccessory steps
                                                                                                  +-- HMActionSetSerializedData
                                                                                                      (protobuf)
```

### Simplified FK diagram

```
ZMKFROOM <---------- ZMKFACCESSORY <-------- ZMKFSERVICE <------- ZMKFCHARACTERISTIC
  (Z_PK)    ZROOM       (Z_PK)   ZACCESSORY    (Z_PK)   ZSERVICE      (Z_PK)
                            ^                      ^                      ^
                            |                      |                      |
                        ZACCESSORY1            ZSERVICE           ZCHARACTERISTICID
                            |                      |                      |
                        ZMKFACTION             ZMKFACTION             ZMKFACTION
                        ZMKFEVENT              ZMKFEVENT              ZMKFEVENT
```

---

## 6. Data Encoding

### 6.1 Binary Plist Columns

Several columns store Apple binary property lists (`bplist00` format). Use
`plistlib.loads()` in Python to decode them.

| Table | Column | Contains |
|-------|--------|----------|
| `ZMKFTRIGGER` | `ZEVALUATIONCONDITION` | NSPredicate defining "any" vs "all" event matching |
| `ZMKFACTION` | `ZTARGETVALUE` | Target value for CharacteristicWriteAction (bool, int, float) |
| `ZMKFACTION` | `ZDATA` | **Full Shortcuts workflow** for ShortcutAction (Z_ENT=40) |
| `ZMKFEVENT` | `ZEVENTVALUE` | Trigger comparison value |

```python
import plistlib

# Decode a target value
target = plistlib.loads(row['ZTARGETVALUE'])
# -> True, False, 100, 0.5, etc.

# Decode a shortcut workflow
workflow = plistlib.loads(row['ZDATA'])
# -> {'WFWorkflowActions': [...]}
```

### 6.2 Shortcuts Workflow Structure (ZDATA)

For `Z_ENT=40` (ShortcutAction) rows, the `ZDATA` binary plist has this structure:

```
{
    "WFWorkflowActions": [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.<type>",
            "WFWorkflowActionParameters": { ... }
        },
        ...
    ]
}
```

#### Known workflow action types

| Identifier | Description | Key Parameters |
|---|---|---|
| `is.workflow.actions.conditional` | If/else/end blocks | `WFControlFlowMode`: 0=if, 1=else, 2=end |
| `is.workflow.actions.homeaccessory` | Set HomeKit device states | `WFHomeTriggerActionSets` containing protobuf data |
| `is.workflow.actions.choosefrommenu` | Multi-branch menu | `WFControlFlowMode`: 0=start, 1=case, 2=end; `WFMenuItems`, `WFMenuItemTitle` |

### 6.3 Protobuf Encoding (HMActionSetSerializedData)

Inside `homeaccessory` workflow steps, the actual device commands are stored
as **Protocol Buffers**, not plists. The path to reach them is:

```
ZDATA (binary plist)
  -> WFWorkflowActions[n]
    -> WFWorkflowActionParameters
      -> WFHomeTriggerActionSets
        -> WFHFTriggerActionSetsBuilderParameterStateActionSets[n]
          -> HMActionSetSerializedData  <-- raw protobuf bytes
```

#### Protobuf field layout

```
message HMActionSetSerializedData {
    // Field 1: Action set UUID (UTF-8 string)
    string action_set_uuid = 1;

    // Field 2: Repeated write actions
    repeated WriteAction write_actions = 2;

    // Field 4: Scene model UUID (16 raw bytes)
    bytes scene_model_id = 4;

    // Field 5: Home model UUID (16 raw bytes)
    bytes home_model_id = 5;
}

message WriteAction {
    // Field 1: Enabled flag
    bool enabled = 1;

    // Field 2: Target value data (contains NSKeyedArchiver bplist)
    bytes target_value = 2;

    // Field 3: Service/characteristic/accessory reference chain
    ReferenceChain references = 3;
}
```

> These are inferred `.proto` definitions based on observed data, not official
> Apple schemas. Field numbers are accurate; message names are descriptive
> labels.

### 6.4 NSKeyedArchiver Within Protobuf

The `target_value` field inside each protobuf `WriteAction` contains an
NSKeyedArchiver-encoded binary plist. Extraction steps:

1. Scan the protobuf bytes for the `bplist00` magic bytes marker
2. Parse from that offset onward as a binary plist
3. Look in the `$objects` array for the actual value
4. Skip `$null` and dict entries -- the remaining scalar values are the targets

```python
import plistlib

def extract_target_value(proto_bytes_field):
    idx = proto_bytes_field.find(b'bplist00')
    if idx < 0:
        return None
    plist = plistlib.loads(proto_bytes_field[idx:])
    if isinstance(plist, dict) and '$objects' in plist:
        for obj in plist['$objects']:
            if obj != '$null' and not isinstance(obj, dict):
                return obj  # The actual value (bool, int, float, string)
    return None
```

### 6.5 CoreData Timestamps

All timestamp columns use Apple's CoreData epoch: **seconds since 2001-01-01
00:00:00 UTC** (NSDate reference date).

```python
import datetime

COREDATA_EPOCH_OFFSET = 978307200  # seconds between Unix epoch and 2001-01-01

def coredata_to_datetime(cd_timestamp):
    """Convert CoreData timestamp to Python datetime."""
    unix_ts = cd_timestamp + COREDATA_EPOCH_OFFSET
    return datetime.datetime.fromtimestamp(unix_ts, tz=datetime.timezone.utc)

# Example: 795000000 -> 2026-03-08T...
```

---

## 7. Critical Gotchas

### Gotcha 1: ZACCESSORY1, not ZACCESSORY

For `CharacteristicWriteAction` rows (Z_ENT=36), the `ZACCESSORY` column is
**always NULL**. The correct foreign key to `ZMKFACCESSORY.Z_PK` is stored in
`ZACCESSORY1`.

```sql
-- WRONG: Returns NULL for every row
SELECT ZACCESSORY FROM ZMKFACTION WHERE Z_ENT = 36;

-- CORRECT: Returns valid accessory FK
SELECT ZACCESSORY1 FROM ZMKFACTION WHERE Z_ENT = 36;
```

This is likely a CoreData model versioning artifact where a relationship was
renamed or migrated, leaving the original column unused. **Always use
`ZACCESSORY1`** for CharacteristicWriteAction rows.

### Gotcha 2: Junction table naming is opaque

The junction table `Z_41TRIGGERS_` and its columns (`Z_41ACTIONSETS_`,
`Z_135TRIGGERS_`) use CoreData's internal relationship IDs, not entity type
IDs. You cannot predict these names from `Z_PRIMARYKEY` alone.

**Always discover junction tables at runtime:**
```sql
-- List all tables
SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'Z_%';

-- Inspect junction table columns
PRAGMA table_info(Z_41TRIGGERS_);
```

### Gotcha 3: UUID namespace isolation (protobuf vs. database)

The UUIDs inside `HMActionSetSerializedData` protobuf blobs form a
**completely separate namespace**. They do NOT match:

- `ZMODELID` in any homed table
- `ZUNIQUEIDENTIFIER` in any homed table
- `uniqueIdentifier` from the `HMHomeManager` API export

Zero overlap was found across 1,419+ ZMODELID lookups. Device identification
within Shortcuts workflows must use **name matching**, not UUID resolution.

### Gotcha 4: HMHomeManager API UUIDs vs. homed UUIDs

The `HMHomeManager` API export produces its own `uniqueIdentifier` values
that also do NOT match homed's `ZUNIQUEIDENTIFIER` values. When correlating
data between the API export and the database, match by **name** (automation
name, accessory name), not by UUID.

### Gotcha 5: Polymorphic tables

`ZMKFACTION` and `ZMKFEVENT` are polymorphic -- they store multiple entity
subtypes in a single table. Always filter by `Z_ENT` to get the correct
subset, and be aware that columns relevant to one subtype will be NULL for
other subtypes.

### Gotcha 6: Read-only access is essential

The homed daemon has the database open and actively writes to it. Always
connect in read-only mode to prevent corruption or WAL conflicts:

```python
conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
```

---

## 8. Useful SQL Queries

### List all entity types

```sql
SELECT Z_ENT, Z_NAME, Z_MAX, Z_SUPER
FROM Z_PRIMARYKEY
ORDER BY Z_ENT;
```

### List all tables with row counts

```sql
SELECT name,
       (SELECT COUNT(*) FROM sqlite_master AS s
        WHERE s.name = m.name) AS exists_flag
FROM sqlite_master m
WHERE type = 'table'
ORDER BY name;
```

### Count automations by type

```sql
SELECT
    CASE Z_ENT
        WHEN 136 THEN 'EventTrigger'
        WHEN 137 THEN 'TimerTrigger'
        ELSE 'Unknown(' || Z_ENT || ')'
    END AS trigger_type,
    COUNT(*) AS count,
    SUM(CASE WHEN ZACTIVE = 1 THEN 1 ELSE 0 END) AS enabled
FROM ZMKFTRIGGER
GROUP BY Z_ENT;
```

### List all automations with their action counts

```sql
SELECT
    t.Z_PK,
    t.ZCONFIGUREDNAME AS automation_name,
    t.ZACTIVE AS enabled,
    CASE t.Z_ENT WHEN 136 THEN 'event' WHEN 137 THEN 'timer' END AS type,
    COUNT(DISTINCT aset.Z_PK) AS action_set_count,
    COUNT(DISTINCT act.Z_PK) AS action_count
FROM ZMKFTRIGGER t
LEFT JOIN Z_41TRIGGERS_ jt ON jt.Z_135TRIGGERS_ = t.Z_PK
LEFT JOIN ZMKFACTIONSET aset ON jt.Z_41ACTIONSETS_ = aset.Z_PK
LEFT JOIN ZMKFACTION act ON act.ZACTIONSET = aset.Z_PK
GROUP BY t.Z_PK
ORDER BY t.ZCONFIGUREDNAME;
```

### Find all Shortcut-type automations

```sql
SELECT
    t.ZCONFIGUREDNAME AS automation_name,
    t.ZACTIVE AS enabled,
    act.Z_PK AS action_pk,
    LENGTH(act.ZDATA) AS workflow_bytes
FROM ZMKFTRIGGER t
JOIN Z_41TRIGGERS_ jt ON jt.Z_135TRIGGERS_ = t.Z_PK
JOIN ZMKFACTIONSET aset ON jt.Z_41ACTIONSETS_ = aset.Z_PK
JOIN ZMKFACTION act ON act.ZACTIONSET = aset.Z_PK
WHERE act.Z_ENT = 40
ORDER BY t.ZCONFIGUREDNAME;
```

### Find all CharacteristicWriteActions with device/room names

```sql
SELECT
    act.Z_PK,
    acc.ZCONFIGUREDNAME AS device_name,
    room.ZNAME AS room_name,
    svc.ZNAME AS service_name,
    chr.ZMANUFACTURERDESCRIPTION AS characteristic,
    chr.ZFORMAT AS value_format
FROM ZMKFACTION act
JOIN ZMKFACCESSORY acc ON act.ZACCESSORY1 = acc.Z_PK
LEFT JOIN ZMKFROOM room ON acc.ZROOM = room.Z_PK
LEFT JOIN ZMKFSERVICE svc ON act.ZSERVICE = svc.Z_PK
LEFT JOIN ZMKFCHARACTERISTIC chr ON act.ZCHARACTERISTICID = chr.Z_PK
WHERE act.Z_ENT = 36
ORDER BY room.ZNAME, acc.ZCONFIGUREDNAME;
```

### List trigger events for a specific automation

```sql
SELECT
    e.Z_PK,
    CASE e.Z_ENT
        WHEN 88 THEN 'calendar'
        WHEN 90 THEN 'charRange'
        WHEN 91 THEN 'charValue'
        WHEN 92 THEN 'duration'
        WHEN 93 THEN 'location'
        WHEN 96 THEN 'presence'
        WHEN 97 THEN 'significantTime'
        ELSE 'unknown(' || e.Z_ENT || ')'
    END AS event_type,
    e.ZSIGNIFICANTEVENT,
    e.ZOFFSETSECONDS,
    chr.ZMANUFACTURERDESCRIPTION AS characteristic,
    acc.ZCONFIGUREDNAME AS device_name
FROM ZMKFEVENT e
LEFT JOIN ZMKFCHARACTERISTIC chr ON e.ZCHARACTERISTICID = chr.Z_PK
LEFT JOIN ZMKFSERVICE svc ON chr.ZSERVICE = svc.Z_PK
LEFT JOIN ZMKFACCESSORY acc ON svc.ZACCESSORY = acc.Z_PK
WHERE e.ZTRIGGER = ?   -- replace ? with trigger Z_PK
ORDER BY e.Z_PK;
```

### List all devices by room

```sql
SELECT
    room.ZNAME AS room_name,
    acc.ZCONFIGUREDNAME AS device_name,
    acc.ZMANUFACTURER,
    acc.ZMODEL,
    COUNT(svc.Z_PK) AS service_count
FROM ZMKFACCESSORY acc
LEFT JOIN ZMKFROOM room ON acc.ZROOM = room.Z_PK
LEFT JOIN ZMKFSERVICE svc ON svc.ZACCESSORY = acc.Z_PK
GROUP BY acc.Z_PK
ORDER BY room.ZNAME, acc.ZCONFIGUREDNAME;
```

### Count actions by type across all automations

```sql
SELECT
    CASE act.Z_ENT
        WHEN 36 THEN 'CharacteristicWrite'
        WHEN 37 THEN 'MatterCommand'
        WHEN 38 THEN 'MediaPlayback'
        WHEN 39 THEN 'NaturalLighting'
        WHEN 40 THEN 'Shortcut'
        ELSE 'Unknown(' || act.Z_ENT || ')'
    END AS action_type,
    COUNT(*) AS count
FROM ZMKFACTION act
GROUP BY act.Z_ENT
ORDER BY count DESC;
```

### Discover junction tables

```sql
-- Find all CoreData junction tables (they follow the Z_NN pattern)
SELECT name FROM sqlite_master
WHERE type = 'table'
  AND name GLOB 'Z_[0-9]*'
ORDER BY name;
```

### Inspect full schema of any table

```sql
-- Replace ZMKFACTION with any table name
PRAGMA table_info(ZMKFACTION);
```

---

*Generated for the homekit-extractor project. Based on observations from a
macOS 15 (Sequoia) system with HomeKit configured. Schema details may vary
across macOS versions due to CoreData model migrations.*
