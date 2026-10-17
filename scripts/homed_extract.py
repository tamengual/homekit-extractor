#!/usr/bin/env python3
"""
homed_extract.py -- Extract HomeKit automation data from Apple's homed daemon database.

Reads ~/Library/HomeKit/core.sqlite (requires Full Disk Access) and outputs
a comprehensive JSON file with all automations including decoded Shortcuts
workflows, protobuf action sets, and NSKeyedArchiver target values.

This script uses ONLY Python stdlib -- no third-party packages required.

Prerequisites:
    - macOS with HomeKit configured
    - Full Disk Access granted to Terminal
      (System Settings > Privacy & Security > Full Disk Access)
    - Python 3.9+

Usage:
    python3 homed_extract.py                         # defaults: ~/Library/HomeKit/core.sqlite -> homekit_homed_export.json
    python3 homed_extract.py -o export.json          # custom output path
    python3 homed_extract.py --db /path/to/core.sqlite
    python3 homed_extract.py --verbose               # print per-automation details to stderr

Technical notes (see also the handoff document):
    - The homed database is a CoreData SQLite store.  All table and column
      names follow CoreData conventions (Z_PK, Z_ENT, ZMKF* prefix, etc.).
    - CharacteristicWriteAction rows use ZACCESSORY1 (not ZACCESSORY) for the
      device foreign key.  ZACCESSORY is always NULL -- likely a CoreData
      model versioning artifact.
    - The trigger-to-action-set relationship goes through a junction table
      named after CoreData's internal entity numbers (Z_41TRIGGERS_ with
      columns Z_41ACTIONSETS_ and Z_135TRIGGERS_ on older macOS, column
      Z_142TRIGGERS_ on macOS 27).  These numbers, and the Z_ENT values of
      every action, event and trigger type, change between macOS releases:
      they are resolved at runtime from Z_PRIMARYKEY and PRAGMA table_info,
      never hard-coded.  An unrecognised schema stops with exit code 4.
    - Range events (thresholds) keep their bounds in ZMIN and ZMAX.
    - Event values, range bounds and characteristic-write target values are
      binary plists on older macOS and Apple OPACK on macOS 27; both decode.
    - ShortcutAction ZDATA blobs are binary plists containing WFWorkflowActions.
    - Within homeaccessory workflow steps, HMActionSetSerializedData is
      protobuf-encoded (NOT plist).  We decode it with a minimal varint-based
      parser -- no protobuf library needed.
    - Target values inside protobuf write actions are NSKeyedArchiver binary
      plists.  We scan for the bplist00 magic bytes and extract from $objects.
    - UUIDs inside protobuf payloads are an isolated namespace -- they do NOT
      match ZMODELID or ZUNIQUEIDENTIFIER in any homed table, nor the
      uniqueIdentifier values from the HMHomeManager API.
    - All CoreData timestamps are seconds since 2001-01-01 00:00:00 UTC.
      Add 978307200 to convert to Unix epoch.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import plistlib
import sqlite3
import struct
import sys
import uuid as uuid_mod

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CoreData epoch: 2001-01-01 00:00:00 UTC expressed as a Unix timestamp.
COREDATA_EPOCH_OFFSET = 978307200

# Friendly names for entity types, keyed by CoreData entity name (Z_PRIMARYKEY.Z_NAME).
# The Z_ENT numbers themselves differ between macOS releases and are read from the database.
ACTION_NAMES = {
    "MKFCharacteristicWriteAction": "characteristicWrite",
    "MKFMatterCommandAction": "matterCommand",
    "MKFMediaPlaybackAction": "mediaPlayback",
    "MKFNaturalLightingAction": "naturalLighting",
    "MKFShortcutAction": "shortcut",
}

EVENT_NAMES = {
    "MKFCalendarEvent": "calendar",
    "MKFCharacteristicEvent": "characteristic",
    "MKFCharacteristicRangeEvent": "charRange",
    "MKFCharacteristicValueEvent": "charValue",
    "MKFDurationEvent": "duration",
    "MKFLocationEvent": "location",
    "MKFMatterAttributeEvent": "matterAttribute",
    "MKFMatterAttributeValueEvent": "matterAttributeValue",
    "MKFPresenceEvent": "presence",
    "MKFSignificantTimeEvent": "significantTime",
}

TRIGGER_NAMES = {
    "MKFEventTrigger": "event",
    "MKFTimerTrigger": "timer",
}

# Entities without which the extraction cannot be trusted.
REQUIRED_ENTITIES = (
    "MKFActionSet",
    "MKFTrigger",
    "MKFEventTrigger",
    "MKFCharacteristicWriteAction",
    "MKFShortcutAction",
)


class SchemaError(Exception):
    """The homed database does not have the structure this script understands."""


@dataclasses.dataclass(frozen=True)
class Schema:
    """CoreData numbering of one homed database, resolved at runtime."""

    entities: dict[str, int]
    trigger_junction: tuple[str, str, str]  # (table, action-set column, trigger column)
    event_columns: frozenset[str]

    def label(self, kind: str, ent: int) -> str:
        """Friendly name of entity *ent* for *kind* ("action", "event" or "trigger").

        An entity unknown to the database is reported as ``ent_<n>``; an entity the
        database names but this script does not map, as ``entity:<name>``.  Never a guess.
        """
        names = {"action": ACTION_NAMES, "event": EVENT_NAMES, "trigger": TRIGGER_NAMES}[kind]
        name = next((n for n, e in self.entities.items() if e == ent), None)
        if name is None:
            return f"ent_{ent}"
        return names.get(name, f"entity:{name}")


def resolve_schema(cur: sqlite3.Cursor) -> Schema:
    """Read entity numbers from Z_PRIMARYKEY and find the trigger/action-set junction table."""
    try:
        rows = cur.execute("SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY").fetchall()
    except sqlite3.OperationalError as exc:
        # Only a missing table means "not a homed schema"; an unreadable file stays a database error.
        if "no such table" not in str(exc):
            raise
        raise SchemaError("no Z_PRIMARYKEY table") from exc
    entities = {name: ent for ent, name in rows if name}
    missing = [name for name in REQUIRED_ENTITIES if name not in entities]
    if missing:
        raise SchemaError("missing entities: " + ", ".join(missing))

    action_set_col = f"Z_{entities['MKFActionSet']}ACTIONSETS_"
    trigger_col = f"Z_{entities['MKFTrigger']}TRIGGERS_"
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'Z\\_%' ESCAPE '\\'").fetchall()]
    candidates = []
    for table in tables:
        quoted = table.replace('"', '""')
        columns = {r[1] for r in cur.execute(f'PRAGMA table_info("{quoted}")').fetchall()}
        if action_set_col in columns and trigger_col in columns:
            candidates.append(table)
    if not candidates:
        raise SchemaError(f"no junction table with columns {action_set_col} and {trigger_col}")
    if len(candidates) > 1:
        raise SchemaError("ambiguous junction tables: " + ", ".join(sorted(candidates)))
    junction = (candidates[0], action_set_col, trigger_col)

    event_columns = frozenset(r[1] for r in cur.execute("PRAGMA table_info(ZMKFEVENT)").fetchall())
    return Schema(entities, junction, event_columns)

# WFCondition lookup (Shortcuts conditional operators).
WF_CONDITIONS = {
    0: "equals",
    1: "notEquals",
    2: "greaterThan",
    3: "lessThan",
    4: "is",
    5: "isNot",
    100: "contains",
    101: "notContains",
    999: "hasAnyValue",
}

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def make_serializable(obj, depth: int = 0):
    """Recursively convert any object to a JSON-serializable form.

    Handles bytes (try plist, then UTF-8, then 16-byte UUID, then hex),
    datetime, plistlib.UID, and nested containers.
    """
    if depth > 20:
        return str(obj)
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return obj
    if isinstance(obj, str):
        return obj

    if isinstance(obj, bytes):
        # Try decoding as an embedded binary plist.
        try:
            inner = plistlib.loads(obj)
            return make_serializable(inner, depth + 1)
        except Exception:
            pass
        # Try plain UTF-8 text.
        try:
            text = obj.decode("utf-8")
            if all(ch.isprintable() or ch in "\n\r\t" for ch in text):
                return text
        except Exception:
            pass
        # 16-byte blob -> UUID.
        if len(obj) == 16:
            try:
                return str(uuid_mod.UUID(bytes=obj))
            except Exception:
                pass
        # Short blobs -> hex representation.
        if len(obj) <= 64:
            return f"hex:{obj.hex()}"
        return f"<{len(obj)} bytes>"

    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [make_serializable(item, depth + 1) for item in obj]
    if isinstance(obj, dict):
        return {str(k): make_serializable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, plistlib.UID):
        return f"UID({obj.data})"

    return str(obj)


# ---------------------------------------------------------------------------
# Minimal protobuf decoder (varint / length-delimited / fixed-width)
# ---------------------------------------------------------------------------

def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a protobuf variable-length integer starting at *offset*.

    Returns (value, new_offset).
    """
    result = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, offset


def parse_protobuf(data: bytes, depth: int = 0) -> dict[int, list[tuple[str, object]]]:
    """Parse raw protobuf bytes into {field_number: [(wire_type_name, value), ...]}.

    Only recurses up to *depth* 5 to avoid runaway parsing on corrupt data.
    """
    if depth > 5:
        return {}
    fields: dict[int, list[tuple[str, object]]] = {}
    i = 0
    while i < len(data):
        try:
            tag, i = _decode_varint(data, i)
        except Exception:
            break
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:  # varint
            val, i = _decode_varint(data, i)
            fields.setdefault(field_num, []).append(("varint", val))
        elif wire_type == 2:  # length-delimited
            length, i = _decode_varint(data, i)
            if i + length > len(data):
                break
            val = data[i : i + length]
            fields.setdefault(field_num, []).append(("bytes", val))
            i += length
        elif wire_type == 1:  # fixed64
            if i + 8 > len(data):
                break
            fields.setdefault(field_num, []).append(("fixed64", data[i : i + 8]))
            i += 8
        elif wire_type == 5:  # fixed32
            if i + 4 > len(data):
                break
            fields.setdefault(field_num, []).append(("fixed32", data[i : i + 4]))
            i += 4
        else:
            break
    return fields


# ---------------------------------------------------------------------------
# NSKeyedArchiver target-value extractor
# ---------------------------------------------------------------------------

def extract_target_value_from_nsarchiver(data: bytes):
    """Extract the actual value from an NSKeyedArchiver-encoded binary plist.

    Strategy: scan for the ``bplist00`` magic bytes, parse from that offset,
    and look in the ``$objects`` array for non-``$null``, non-dict values.
    These are the concrete target values (booleans, numbers, strings).
    """
    bp_idx = data.find(b"bplist")
    if bp_idx < 0:
        return None
    try:
        plist = plistlib.loads(data[bp_idx:])
        if isinstance(plist, dict) and "$objects" in plist:
            for obj in plist["$objects"]:
                if obj != "$null" and not isinstance(obj, dict):
                    return make_serializable(obj)
        return make_serializable(plist)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Value decoding: binary plist (older macOS) or OPACK (macOS 27)
# ---------------------------------------------------------------------------

def _take(data: bytes, offset: int, size: int) -> bytes:
    if offset + size > len(data):
        raise ValueError("truncated OPACK value")
    return data[offset:offset + size]


OPACK_MAX_DEPTH = 32


def _decode_opack_at(data: bytes, offset: int, depth: int = 0):
    """Decode one OPACK object at *offset*; return (value, new_offset).

    Only well-understood tags are decoded; anything else raises ValueError, so the caller keeps the raw bytes.
    """
    if depth > OPACK_MAX_DEPTH:
        raise ValueError("OPACK nesting too deep")
    tag = _take(data, offset, 1)[0]
    i = offset + 1
    if tag == 0x01:
        return True, i
    if tag == 0x02:
        return False, i
    if tag == 0x04:
        return None, i
    if tag == 0x05:
        return str(uuid_mod.UUID(bytes=_take(data, i, 16))), i + 16
    if 0x08 <= tag <= 0x2F:
        return tag - 0x08, i
    if 0x30 <= tag <= 0x33:
        size = 1 << (tag - 0x30)
        return int.from_bytes(_take(data, i, size), "little"), i + size
    if tag == 0x35:  # float32: trimmed to its 7 significant digits (18.3, not 18.299999237060547)
        return float(f"{struct.unpack('<f', _take(data, i, 4))[0]:.7g}"), i + 4
    if tag == 0x36:
        return struct.unpack("<d", _take(data, i, 8))[0], i + 8
    if 0x40 <= tag <= 0x64:
        if tag <= 0x60:
            size = tag - 0x40
        else:
            width = tag - 0x60
            size = int.from_bytes(_take(data, i, width), "little")
            i += width
        return _take(data, i, size).decode("utf-8"), i + size
    if 0x70 <= tag <= 0x94:
        if tag <= 0x90:
            size = tag - 0x70
        else:
            width = 1 << (tag - 0x91)
            size = int.from_bytes(_take(data, i, width), "little")
            i += width
        return make_serializable(_take(data, i, size)), i + size
    if 0xD0 <= tag <= 0xDF:
        items: list = []
        while (tag < 0xDF and len(items) < tag - 0xD0) or (tag == 0xDF and _take(data, i, 1)[0] != 0x03):
            item, i = _decode_opack_at(data, i, depth + 1)
            items.append(item)
        return items, (i + 1 if tag == 0xDF else i)
    if 0xE0 <= tag <= 0xEF:
        mapping: dict = {}
        pairs = 0
        while (tag < 0xEF and pairs < tag - 0xE0) or (tag == 0xEF and _take(data, i, 1)[0] != 0x03):
            key, i = _decode_opack_at(data, i, depth + 1)
            value, i = _decode_opack_at(data, i, depth + 1)
            mapping[str(key)] = value
            pairs += 1
        return mapping, (i + 1 if tag == 0xEF else i)
    raise ValueError(f"unknown OPACK tag 0x{tag:02x}")


def decode_opack(data: bytes):
    """Decode an Apple OPACK value (how macOS 27 stores event, bound and target values)."""
    value, end = _decode_opack_at(data, 0)
    if end != len(data):
        raise ValueError("trailing bytes after OPACK value")
    return value


def decode_value(data: bytes):
    """Decode a stored value (ZEVENTVALUE, ZMIN / ZMAX, ZTARGETVALUE).

    Older databases use binary plists (possibly NSKeyedArchiver archives); macOS 27 uses OPACK.
    A value that cannot be decoded is kept as ``opack:<hex>`` / ``bplist:<hex>`` rather than dropped.
    """
    if data.startswith(b"bplist"):
        try:
            value = plistlib.loads(data)
        except Exception:
            return f"bplist:{data.hex()}"
        if isinstance(value, dict) and "$objects" in value:
            return extract_target_value_from_nsarchiver(data)
        return make_serializable(value)
    try:
        return make_serializable(decode_opack(data))
    except (ValueError, IndexError, UnicodeDecodeError, OverflowError, RecursionError, struct.error):
        return f"opack:{data.hex()}"


# ---------------------------------------------------------------------------
# HMActionSetSerializedData protobuf decoder
# ---------------------------------------------------------------------------

def decode_action_set_proto(proto_data: bytes) -> dict:
    """Decode an HMActionSetSerializedData protobuf into readable actions.

    Protobuf layout:
        Field 1 (bytes/string): action-set UUID
        Field 2 (bytes, repeated): individual write actions
            Sub-field 1 (varint): enabled flag
            Sub-field 2 (bytes): target-value data (NSKeyedArchiver bplist
                                 + characteristic reference UUID)
            Sub-field 3 (bytes): service/characteristic/accessory reference
                                 chain (nested 16-byte UUIDs)
        Field 4 (bytes, 16B): scene model UUID
        Field 5 (bytes, 16B): home model UUID
    """
    fields = parse_protobuf(proto_data)
    result: dict = {}

    # -- Field 1: action-set UUID string ---------------------------------
    if 1 in fields:
        for wt, val in fields[1]:
            if wt == "bytes":
                try:
                    result["actionSetUUID"] = val.decode("ascii")
                except Exception:
                    pass

    # -- Field 2: individual write actions (repeated) --------------------
    write_actions: list[dict] = []
    if 2 in fields:
        for wt, val in fields[2]:
            if wt != "bytes":
                continue
            action_fields = parse_protobuf(val)
            wa: dict = {}

            # Enabled flag
            if 1 in action_fields:
                for awt, aval in action_fields[1]:
                    if awt == "varint":
                        wa["enabled"] = bool(aval)

            # Target value data (NSKeyedArchiver + characteristic ref)
            if 2 in action_fields:
                for awt, aval in action_fields[2]:
                    if awt == "bytes":
                        tv = extract_target_value_from_nsarchiver(aval)
                        if tv is not None:
                            wa["targetValue"] = tv
                        # Characteristic reference UUID inside nested proto
                        inner = parse_protobuf(aval)
                        if 1 in inner:
                            for iwt, ival in inner[1]:
                                if iwt == "bytes" and len(ival) == 16:
                                    wa["characteristicModelID"] = str(
                                        uuid_mod.UUID(bytes=ival)
                                    )

            # Service / characteristic / accessory reference chain
            if 3 in action_fields:
                for awt, aval in action_fields[3]:
                    if awt == "bytes":
                        ref = parse_protobuf(aval)
                        chain: list[str] = []
                        if 1 in ref:
                            for rwt, rval in ref[1]:
                                if rwt == "bytes":
                                    inner2 = parse_protobuf(rval)
                                    for _fn2, fvals2 in inner2.items():
                                        for rwt2, rval2 in fvals2:
                                            if rwt2 == "bytes" and len(rval2) == 16:
                                                chain.append(
                                                    str(uuid_mod.UUID(bytes=rval2))
                                                )
                        if 2 in ref:
                            for rwt, rval in ref[2]:
                                if rwt == "bytes" and len(rval) == 16:
                                    chain.append(str(uuid_mod.UUID(bytes=rval)))
                        if chain:
                            wa["referenceChain"] = chain

            if wa:
                write_actions.append(wa)

    if write_actions:
        result["writeActions"] = write_actions

    # -- Fields 4/5: scene and home model UUIDs --------------------------
    for fn, label in [(4, "sceneModelID"), (5, "homeModelID")]:
        if fn in fields:
            for wt, val in fields[fn]:
                if wt == "bytes" and len(val) == 16:
                    result[label] = str(uuid_mod.UUID(bytes=val))

    return result


# ---------------------------------------------------------------------------
# Shortcuts workflow decoder
# ---------------------------------------------------------------------------

def decode_shortcut_workflow(data: bytes) -> list[dict]:
    """Decode a ShortcutAction ZDATA blob into structured workflow steps.

    ZDATA is a binary plist containing ``WFWorkflowActions`` -- the same
    format used by the Shortcuts app.  Known action identifiers:

        is.workflow.actions.conditional
            WFControlFlowMode: 0 = if, 1 = else, 2 = end
        is.workflow.actions.homeaccessory
            Contains HMActionSetSerializedData protobuf blobs.
        is.workflow.actions.choosefrommenu
            WFControlFlowMode: 0 = start, 1 = case, 2 = end
    """
    parsed = plistlib.loads(data)
    wf_actions = parsed.get("WFWorkflowActions", [])

    steps: list[dict] = []
    for step in wf_actions:
        ident = step.get("WFWorkflowActionIdentifier", "").replace(
            "is.workflow.actions.", ""
        )
        params = step.get("WFWorkflowActionParameters", {})
        s: dict = {"type": ident}

        if ident == "conditional":
            mode = params.get("WFControlFlowMode", -1)
            s["mode"] = {0: "if", 1: "else", 2: "end"}.get(mode, f"mode_{mode}")
            if mode == 0:
                cond = params.get("WFCondition", "")
                s["condition"] = WF_CONDITIONS.get(cond, f"cond_{cond}")
                wf_input = params.get("WFInput")
                s["inputType"] = (
                    wf_input.get("Type", "") if isinstance(wf_input, dict) else ""
                )
                if "WFConditionalActionString" in params:
                    s["conditionValue"] = str(params["WFConditionalActionString"])
                if "WFNumberValue" in params:
                    s["conditionValue"] = make_serializable(params["WFNumberValue"])

        elif ident == "homeaccessory":
            asets = params.get("WFHomeTriggerActionSets", {})
            if isinstance(asets, dict):
                state = asets.get(
                    "WFHFTriggerActionSetsBuilderParameterStateActionSets", []
                )
                decoded_asets: list[dict] = []
                if isinstance(state, list):
                    for entry in state:
                        if isinstance(entry, dict):
                            proto = entry.get("HMActionSetSerializedData")
                            if proto and isinstance(proto, bytes):
                                decoded_asets.append(decode_action_set_proto(proto))
                s["actionSets"] = decoded_asets

        elif ident == "choosefrommenu":
            mode = params.get("WFControlFlowMode", -1)
            s["mode"] = {0: "start", 1: "case", 2: "end"}.get(mode, f"mode_{mode}")
            if "WFMenuItems" in params:
                s["menuItems"] = make_serializable(params["WFMenuItems"])
            if "WFMenuItemTitle" in params:
                s["menuItemTitle"] = make_serializable(params["WFMenuItemTitle"])

        steps.append(s)
    return steps


# ---------------------------------------------------------------------------
# Lookup-table builders
# ---------------------------------------------------------------------------

def _build_room_lookup(cur: sqlite3.Cursor) -> dict[int, str]:
    """Z_PK -> room name."""
    cur.execute("SELECT Z_PK, ZNAME FROM ZMKFROOM")
    return {row[0]: row[1] for row in cur.fetchall()}


def _build_accessory_lookup(
    cur: sqlite3.Cursor, rooms: dict[int, str]
) -> dict[int, dict]:
    """Z_PK -> {name, manufacturer, model, uuid, room}."""
    lookup: dict[int, dict] = {}
    cur.execute(
        "SELECT Z_PK, ZCONFIGUREDNAME, ZMANUFACTURER, ZMODEL, "
        "ZUNIQUEIDENTIFIER, ZROOM FROM ZMKFACCESSORY"
    )
    for pk, name, mfr, model, uid, room_pk in cur.fetchall():
        lookup[pk] = {
            "name": name,
            "manufacturer": mfr,
            "model": model,
            "uuid": uid,
            "room": rooms.get(room_pk, "") if room_pk else "",
        }
    return lookup


def _build_service_lookup(
    cur: sqlite3.Cursor, accessories: dict[int, dict]
) -> dict[int, dict]:
    """Z_PK -> {name, accessory}."""
    lookup: dict[int, dict] = {}
    cur.execute(
        "SELECT Z_PK, ZNAME, ZACCESSORY, ZEXPECTEDCONFIGUREDNAME FROM ZMKFSERVICE"
    )
    for pk, name, acc_pk, configured_name in cur.fetchall():
        acc_info = accessories.get(acc_pk, {})
        lookup[pk] = {
            "name": configured_name or name,
            "accessory": acc_info.get("name", ""),
        }
    return lookup


def _build_characteristic_lookup(
    cur: sqlite3.Cursor, services: dict[int, dict]
) -> dict[int, dict]:
    """Z_PK -> {type, service, accessory, description, format}."""
    lookup: dict[int, dict] = {}
    cur.execute(
        "SELECT Z_PK, ZTYPE, ZSERVICE, ZMANUFACTURERDESCRIPTION, ZFORMAT "
        "FROM ZMKFCHARACTERISTIC"
    )
    for pk, ztype, svc_pk, desc, fmt in cur.fetchall():
        char_type = None
        if ztype:
            try:
                char_type = str(uuid_mod.UUID(bytes=ztype))
            except Exception:
                pass
        svc_info = services.get(svc_pk, {})
        lookup[pk] = {
            "type": char_type,
            "service": svc_info.get("name", ""),
            "accessory": svc_info.get("accessory", ""),
            "description": desc,
            "format": fmt,
        }
    return lookup


# ---------------------------------------------------------------------------
# Event extraction
# ---------------------------------------------------------------------------

def _extract_events(
    cur: sqlite3.Cursor,
    schema: Schema,
    trigger_pk: int,
    char_info: dict[int, dict],
    svc_names: dict[int, dict],
) -> list[dict]:
    """Return a list of decoded event dicts for a given trigger Z_PK."""
    has_bounds = {"ZMIN", "ZMAX"} <= schema.event_columns
    bounds = ", e.ZMIN, e.ZMAX" if has_bounds else ", NULL, NULL"
    cur.execute(
        "SELECT e.Z_ENT, e.ZSIGNIFICANTEVENT, e.ZPRESENCETYPE, e.ZOFFSETSECONDS, "
        "e.ZDURATION, e.ZEVENTVALUE, e.ZCHARACTERISTICID, e.ZSERVICE" + bounds + " "
        "FROM ZMKFEVENT e WHERE e.ZTRIGGER = ?",
        (trigger_pk,),
    )
    events: list[dict] = []
    for ev in cur.fetchall():
        evt: dict = {
            "eventType": schema.label("event", ev[0])
        }
        for column, key in ((8, "minimum"), (9, "maximum")):
            if ev[column] is not None:
                evt[key] = decode_value(ev[column])
        if ev[1]:
            evt["significantEvent"] = ev[1]
        if ev[2] is not None:
            evt["presenceType"] = ev[2]
        if ev[3] is not None:
            evt["offsetSeconds"] = ev[3]
        if ev[5]:
            evt["eventValue"] = decode_value(ev[5])
        if ev[6]:
            ci = char_info.get(ev[6], {})
            evt["characteristic"] = ci.get("description") or ci.get("type", "")
            evt["accessory"] = ci.get("accessory", "")
            evt["service"] = ci.get("service", "")
            evt["format"] = ci.get("format", "")
        if ev[7]:
            si = svc_names.get(ev[7], {})
            if not evt.get("accessory"):
                evt["accessory"] = si.get("accessory", "")
        events.append(evt)
    return events


# ---------------------------------------------------------------------------
# Action extraction
# ---------------------------------------------------------------------------

def _extract_actions(
    cur: sqlite3.Cursor,
    schema: Schema,
    action_set_pk: int,
    char_info: dict[int, dict],
    acc_names: dict[int, dict],
    svc_names: dict[int, dict],
    verbose: bool = False,
) -> list[dict]:
    """Return a list of decoded action dicts for a given action-set Z_PK.

    IMPORTANT: We query ZACCESSORY1 (not ZACCESSORY) because all
    CharacteristicWriteAction rows have NULL in ZACCESSORY but a valid
    foreign key in ZACCESSORY1.  This is a CoreData model versioning
    artifact.
    """
    cur.execute(
        "SELECT Z_PK, Z_ENT, ZDATA, ZTARGETVALUE, ZCHARACTERISTICID, "
        "ZACCESSORY1, ZSERVICE "
        "FROM ZMKFACTION WHERE ZACTIONSET = ?",
        (action_set_pk,),
    )
    actions: list[dict] = []
    for act_pk, act_ent, act_data, act_target, act_char, act_acc, act_svc in cur.fetchall():
        atype = schema.label("action", act_ent)
        ad: dict = {"actionType": atype}

        if atype == "shortcut" and act_data:
            try:
                ad["workflowSteps"] = decode_shortcut_workflow(act_data)
            except Exception as exc:
                ad["decodeError"] = str(exc)
                if verbose:
                    print(
                        f"  [!] Failed to decode ShortcutAction Z_PK={act_pk}: {exc}",
                        file=sys.stderr,
                    )

        elif atype == "characteristicWrite":
            if act_target:
                ad["targetValue"] = decode_value(act_target)
            if act_char:
                ci = char_info.get(act_char, {})
                ad["characteristic"] = ci.get("description") or ci.get("type", "")
                ad["format"] = ci.get("format", "")
            if act_acc:
                ai = acc_names.get(act_acc, {})
                ad["accessoryName"] = ai.get("name", "")
                ad["room"] = ai.get("room", "")
            if act_svc:
                si = svc_names.get(act_svc, {})
                ad["serviceName"] = si.get("name", "")

        actions.append(ad)
    return actions


# ---------------------------------------------------------------------------
# Main extraction logic
# ---------------------------------------------------------------------------

def extract(db_path: str, verbose: bool = False) -> dict:
    """Open *db_path* read-only and return the full extraction dict."""

    # Verify the file exists before trying to open it.
    if not os.path.isfile(db_path):
        raise FileNotFoundError(
            f"Database not found: {db_path}\n"
            "Make sure HomeKit is configured on this Mac and the path is correct."
        )

    # Open in read-only mode so we can never accidentally mutate the store.
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        if "unable to open" in str(exc).lower():
            raise PermissionError(
                f"Cannot open database (permission denied): {db_path}\n"
                "Grant Full Disk Access to Terminal:\n"
                "  System Settings > Privacy & Security > Full Disk Access > + Terminal"
            ) from exc
        raise
    cur = conn.cursor()

    # -- Resolve this database's CoreData numbering --------------------------
    try:
        schema = resolve_schema(cur)
    except SchemaError:
        conn.close()
        raise

    # -- Build lookup tables ------------------------------------------------
    if verbose:
        print("[*] Building lookup tables ...", file=sys.stderr)

    rooms = _build_room_lookup(cur)
    acc_names = _build_accessory_lookup(cur, rooms)
    svc_names = _build_service_lookup(cur, acc_names)
    char_info = _build_characteristic_lookup(cur, svc_names)

    if verbose:
        print(
            f"    {len(rooms)} rooms, {len(acc_names)} accessories, "
            f"{len(svc_names)} services, {len(char_info)} characteristics",
            file=sys.stderr,
        )

    # -- Extract automations ------------------------------------------------
    if verbose:
        print("[*] Extracting automations ...", file=sys.stderr)

    cur.execute(
        "SELECT t.Z_PK, t.Z_ENT, t.ZNAME, t.ZCONFIGUREDNAME, t.ZACTIVE, "
        "t.ZEVALUATIONCONDITION, t.ZSIGNIFICANTEVENT, "
        "t.ZSIGNIFICANTEVENTOFFSETSECONDS, t.ZMOSTRECENTFIREDATE, t.ZEXECUTEONCE "
        "FROM ZMKFTRIGGER t ORDER BY t.ZCONFIGUREDNAME"
    )
    triggers = cur.fetchall()

    all_automations: list[dict] = []
    for trig in triggers:
        (
            t_pk,
            t_ent,
            t_name,
            t_cname,
            t_active,
            t_eval,
            t_sig,
            t_sig_off,
            t_recent,
            t_once,
        ) = trig

        auto: dict = {
            "name": t_cname or t_name,
            "enabled": bool(t_active),
            "triggerType": schema.label("trigger", t_ent),
        }

        if t_eval:
            try:
                auto["evaluationCondition"] = make_serializable(plistlib.loads(t_eval))
            except Exception:
                pass
        if t_sig:
            auto["significantEvent"] = t_sig
        if t_sig_off is not None:
            auto["significantEventOffsetSeconds"] = t_sig_off
        if t_once is not None:
            auto["executeOnce"] = bool(t_once)
        if t_recent is not None:
            try:
                auto["mostRecentFireDate"] = datetime.datetime.utcfromtimestamp(
                    t_recent + COREDATA_EPOCH_OFFSET
                ).isoformat() + "Z"
            except Exception:
                pass

        # Events (trigger conditions)
        auto["events"] = _extract_events(cur, schema, t_pk, char_info, svc_names)

        # Action sets via the junction table resolved by resolve_schema():
        # its action-set column -> ZMKFACTIONSET.Z_PK, its trigger column -> ZMKFTRIGGER.Z_PK.
        junction, action_set_col, trigger_col = schema.trigger_junction
        cur.execute(
            "SELECT aset.Z_PK, aset.ZNAME, aset.ZTYPE "
            f'FROM "{junction}" jt '
            f'JOIN ZMKFACTIONSET aset ON jt."{action_set_col}" = aset.Z_PK '
            f'WHERE jt."{trigger_col}" = ?',
            (t_pk,),
        )

        auto["actionSets"] = []
        for aset_pk, aset_name, aset_type in cur.fetchall():
            asd: dict = {
                "name": aset_name or "",
                "type": aset_type or "",
                "actions": _extract_actions(
                    cur, schema, aset_pk, char_info, acc_names, svc_names, verbose
                ),
            }
            auto["actionSets"].append(asd)

        if verbose:
            n_actions = sum(len(a["actions"]) for a in auto["actionSets"])
            has_shortcut = any(
                act.get("actionType") == "shortcut"
                for aset in auto["actionSets"]
                for act in aset["actions"]
            )
            marker = " [SHORTCUT]" if has_shortcut else ""
            print(
                f"    {auto['name']}: {len(auto['events'])} events, "
                f"{len(auto['actionSets'])} action sets, {n_actions} actions{marker}",
                file=sys.stderr,
            )

        all_automations.append(auto)

    conn.close()

    # -- Compute summary stats ----------------------------------------------
    shortcut_autos = [
        a
        for a in all_automations
        if any(
            act.get("actionType") == "shortcut"
            for aset in a.get("actionSets", [])
            for act in aset.get("actions", [])
        )
    ]

    total_cw = sum(
        1
        for a in all_automations
        for s in a.get("actionSets", [])
        for act in s.get("actions", [])
        if act.get("actionType") == "characteristicWrite"
    )

    total_wf_steps = sum(
        len(act.get("workflowSteps", []))
        for a in all_automations
        for s in a.get("actionSets", [])
        for act in s.get("actions", [])
        if act.get("actionType") == "shortcut"
    )

    output = {
        "extractionSource": "homed_core.sqlite",
        "extractionDate": datetime.datetime.now().isoformat(),
        "databasePath": db_path,
        "stats": {
            "totalAutomations": len(all_automations),
            "shortcutAutomations": len(shortcut_autos),
            "characteristicWriteActions": total_cw,
            "totalWorkflowSteps": total_wf_steps,
            "rooms": len(rooms),
            "accessories": len(acc_names),
            "services": len(svc_names),
            "characteristics": len(char_info),
        },
        "automations": all_automations,
    }
    return output


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract HomeKit automation data from Apple's homed daemon database. "
            "Outputs a comprehensive JSON file including decoded Shortcuts "
            "workflows, protobuf action sets, and NSKeyedArchiver target values."
        ),
        epilog=(
            "Requires macOS Full Disk Access for Terminal. "
            "Grant via: System Settings > Privacy & Security > Full Disk Access."
        ),
    )
    parser.add_argument(
        "--db",
        default=os.path.expanduser("~/Library/HomeKit/core.sqlite"),
        help=(
            "Path to the homed CoreData database. "
            "Default: ~/Library/HomeKit/core.sqlite"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default="homekit_homed_export.json",
        help="Output JSON file path. Default: homekit_homed_export.json",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-automation details to stderr while extracting.",
    )
    args = parser.parse_args()

    # -- Run extraction -----------------------------------------------------
    print(f"[*] Database: {args.db}", file=sys.stderr)
    try:
        output = extract(args.db, verbose=args.verbose)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except PermissionError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)
    except SchemaError as exc:
        print(f"[ERROR] Unsupported homed schema: {exc}", file=sys.stderr)
        sys.exit(4)
    except sqlite3.DatabaseError as exc:
        print(f"[ERROR] Database error: {exc}", file=sys.stderr)
        sys.exit(3)

    # -- Write JSON ---------------------------------------------------------
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, default=str)

    size_kb = os.path.getsize(args.output) / 1024

    # -- Summary to stderr --------------------------------------------------
    stats = output["stats"]
    print(f"[*] Exported to: {args.output} ({size_kb:.1f} KB)", file=sys.stderr)
    print(f"[*] Summary:", file=sys.stderr)
    print(f"    Automations:               {stats['totalAutomations']}", file=sys.stderr)
    print(f"    Shortcut automations:       {stats['shortcutAutomations']}", file=sys.stderr)
    print(f"    CharacteristicWrite actions: {stats['characteristicWriteActions']}", file=sys.stderr)
    print(f"    Total workflow steps:        {stats['totalWorkflowSteps']}", file=sys.stderr)
    print(f"    Rooms:                       {stats['rooms']}", file=sys.stderr)
    print(f"    Accessories:                 {stats['accessories']}", file=sys.stderr)
    print(f"    Services:                    {stats['services']}", file=sys.stderr)
    print(f"    Characteristics:             {stats['characteristics']}", file=sys.stderr)


if __name__ == "__main__":
    main()
