"""Synthetic homed (CoreData) databases for tests.

build_db() creates the tables and columns that homed_extract.py reads, numbered
like a given macOS release, and fills them with invented values only.
"""

import plistlib
import sqlite3
import struct
import uuid

# Entity numbers hard-coded by the original homed_extract.py.
NUMBERING_ORIGINAL = {
    "MKFCharacteristicWriteAction": 36,
    "MKFMatterCommandAction": 37,
    "MKFMediaPlaybackAction": 38,
    "MKFNaturalLightingAction": 39,
    "MKFShortcutAction": 40,
    "MKFActionSet": 41,
    "MKFCalendarEvent": 88,
    "MKFCharacteristicRangeEvent": 90,
    "MKFCharacteristicValueEvent": 91,
    "MKFDurationEvent": 92,
    "MKFLocationEvent": 93,
    "MKFPresenceEvent": 96,
    "MKFSignificantTimeEvent": 97,
    "MKFTrigger": 135,
    "MKFEventTrigger": 136,
    "MKFTimerTrigger": 137,
}

# Entity numbers observed in a macOS 27 homed database.
NUMBERING_MACOS27 = {
    "MKFCharacteristicWriteAction": 36,
    "MKFMatterCommandAction": 37,
    "MKFMediaPlaybackAction": 38,
    "MKFNaturalLightingAction": 39,
    "MKFShortcutAction": 40,
    "MKFActionSet": 41,
    "MKFCalendarEvent": 90,
    "MKFCharacteristicEvent": 91,
    "MKFCharacteristicRangeEvent": 92,
    "MKFCharacteristicValueEvent": 93,
    "MKFDurationEvent": 94,
    "MKFLocationEvent": 95,
    "MKFMatterAttributeEvent": 96,
    "MKFMatterAttributeValueEvent": 97,
    "MKFPresenceEvent": 98,
    "MKFSignificantTimeEvent": 99,
    "MKFRoom": 136,
    "MKFTrigger": 142,
    "MKFEventTrigger": 143,
    "MKFTimerTrigger": 144,
    "MKFZone": 149,
}

POWER_STATE = uuid.UUID("00000025-0000-1000-8000-0026BB765291").bytes
CURRENT_TEMPERATURE = uuid.UUID("00000011-0000-1000-8000-0026BB765291").bytes


def bplist(value) -> bytes:
    return plistlib.dumps(value, fmt=plistlib.FMT_BINARY)


def opack(value) -> bytes:
    """Encode a bool, small int or float as Apple OPACK, as macOS 27 stores values."""
    if value is True:
        return b"\x01"
    if value is False:
        return b"\x02"
    if isinstance(value, int) and 0 <= value <= 39:
        return bytes([0x08 + value])
    if isinstance(value, float):
        return b"\x35" + struct.pack("<f", value)
    raise ValueError(f"no OPACK encoding for {value!r} in this fixture")


def shortcut_workflow() -> bytes:
    """A Shortcuts workflow: if / homeaccessory / else / end."""
    prefix = "is.workflow.actions."
    steps = [
        {"WFWorkflowActionIdentifier": prefix + "conditional",
         "WFWorkflowActionParameters": {"WFControlFlowMode": 0, "WFCondition": 4,
                                        "WFInput": {"Type": "HomeAccessory"}}},
        {"WFWorkflowActionIdentifier": prefix + "homeaccessory", "WFWorkflowActionParameters": {}},
        {"WFWorkflowActionIdentifier": prefix + "conditional",
         "WFWorkflowActionParameters": {"WFControlFlowMode": 1}},
        {"WFWorkflowActionIdentifier": prefix + "conditional",
         "WFWorkflowActionParameters": {"WFControlFlowMode": 2}},
    ]
    return bplist({"WFWorkflowActions": steps})


def build_db(path: str, numbering: dict, values: str = "bplist") -> None:
    """Create a homed-like database at *path*, numbered with *numbering*.

    *values* is how event values, range bounds and target values are stored:
    "bplist" (older macOS) or "opack" (macOS 27).

    Contents: one room ("Room 1"), two accessories ("Lamp 1", "Sensor 1"),
    and two active automations:
      - "Automation 1": value event on Sensor 1 power, writes True to Lamp 1 power;
      - "Automation 2": range event (18..22) on Sensor 1 temperature, runs a shortcut.
    """
    ent = numbering.get
    encode = opack if values == "opack" else bplist
    action_set = numbering["MKFActionSet"]
    trigger = numbering["MKFTrigger"]
    junction = f"Z_{action_set}TRIGGERS_"
    as_col = f"Z_{action_set}ACTIONSETS_"
    tr_col = f"Z_{trigger}TRIGGERS_"

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(f"""
        CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER PRIMARY KEY, Z_NAME VARCHAR, Z_SUPER INTEGER, Z_MAX INTEGER);
        CREATE TABLE ZMKFROOM (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZNAME VARCHAR);
        CREATE TABLE ZMKFACCESSORY (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZCONFIGUREDNAME VARCHAR,
            ZMANUFACTURER VARCHAR, ZMODEL VARCHAR, ZUNIQUEIDENTIFIER VARCHAR, ZROOM INTEGER);
        CREATE TABLE ZMKFSERVICE (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZNAME VARCHAR, ZACCESSORY INTEGER,
            ZEXPECTEDCONFIGUREDNAME VARCHAR);
        CREATE TABLE ZMKFCHARACTERISTIC (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZTYPE BLOB, ZSERVICE INTEGER,
            ZMANUFACTURERDESCRIPTION VARCHAR, ZFORMAT VARCHAR);
        CREATE TABLE ZMKFTRIGGER (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZNAME VARCHAR, ZCONFIGUREDNAME VARCHAR,
            ZACTIVE INTEGER, ZEVALUATIONCONDITION BLOB, ZSIGNIFICANTEVENT VARCHAR,
            ZSIGNIFICANTEVENTOFFSETSECONDS FLOAT, ZMOSTRECENTFIREDATE TIMESTAMP, ZEXECUTEONCE INTEGER);
        CREATE TABLE ZMKFEVENT (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZTRIGGER INTEGER, ZSIGNIFICANTEVENT VARCHAR,
            ZPRESENCETYPE VARCHAR, ZOFFSETSECONDS FLOAT, ZDURATION FLOAT, ZEVENTVALUE BLOB,
            ZCHARACTERISTICID INTEGER, ZSERVICE INTEGER, ZMIN BLOB, ZMAX BLOB);
        CREATE TABLE ZMKFACTIONSET (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZNAME VARCHAR, ZTYPE VARCHAR);
        CREATE TABLE ZMKFACTION (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZACTIONSET INTEGER, ZDATA BLOB,
            ZTARGETVALUE BLOB, ZCHARACTERISTICID INTEGER, ZACCESSORY1 INTEGER, ZSERVICE INTEGER);
        CREATE TABLE {junction} ({as_col} INTEGER, {tr_col} INTEGER);
    """)
    cur.executemany("INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME) VALUES (?, ?)",
                    [(number, name) for name, number in numbering.items()])
    cur.execute("INSERT INTO ZMKFROOM VALUES (1, ?, 'Room 1')", (ent("MKFRoom"),))
    cur.executemany("INSERT INTO ZMKFACCESSORY VALUES (?, NULL, ?, 'Maker 1', 'Model 1', ?, 1)",
                    [(1, "Lamp 1", "LAMP-UUID-1"), (2, "Sensor 1", "SENSOR-UUID-1")])
    cur.executemany("INSERT INTO ZMKFSERVICE VALUES (?, NULL, ?, ?, NULL)",
                    [(1, "Lamp 1 Light", 1), (2, "Sensor 1 Sensor", 2)])
    cur.executemany("INSERT INTO ZMKFCHARACTERISTIC VALUES (?, NULL, ?, ?, ?, ?)",
                    [(1, POWER_STATE, 1, "Power State", "bool"),
                     (2, POWER_STATE, 2, "Power State", "bool"),
                     (3, CURRENT_TEMPERATURE, 2, "Current Temperature", "float")])
    cur.executemany("INSERT INTO ZMKFTRIGGER (Z_PK, Z_ENT, ZNAME, ZCONFIGUREDNAME, ZACTIVE) VALUES (?, ?, ?, ?, 1)",
                    [(1, ent("MKFEventTrigger"), "Automation 1", "Automation 1"),
                     (2, ent("MKFEventTrigger"), "Automation 2", "Automation 2")])
    cur.execute("INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZEVENTVALUE, ZCHARACTERISTICID, ZSERVICE) "
                "VALUES (1, ?, 1, ?, 2, 2)", (ent("MKFCharacteristicValueEvent"), encode(True)))
    cur.execute("INSERT INTO ZMKFEVENT (Z_PK, Z_ENT, ZTRIGGER, ZCHARACTERISTICID, ZSERVICE, ZMIN, ZMAX) "
                "VALUES (2, ?, 2, 3, 2, ?, ?)", (ent("MKFCharacteristicRangeEvent"), encode(18.0), encode(22.0)))
    cur.executemany("INSERT INTO ZMKFACTIONSET VALUES (?, ?, '', 'HMActionSetTypeTriggerOwned')",
                    [(1, action_set), (2, action_set)])
    cur.execute("INSERT INTO ZMKFACTION (Z_PK, Z_ENT, ZACTIONSET, ZTARGETVALUE, ZCHARACTERISTICID, ZACCESSORY1, "
                "ZSERVICE) VALUES (1, ?, 1, ?, 1, 1, 1)", (ent("MKFCharacteristicWriteAction"), encode(True)))
    cur.execute("INSERT INTO ZMKFACTION (Z_PK, Z_ENT, ZACTIONSET, ZDATA) VALUES (2, ?, 2, ?)",
                (ent("MKFShortcutAction"), shortcut_workflow()))
    cur.executemany(f"INSERT INTO {junction} VALUES (?, ?)", [(1, 1), (2, 2)])
    conn.commit()
    conn.close()
