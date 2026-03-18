#!/usr/bin/env python3
"""Build HomeKit accessory -> HA entity_id map using device-centric matching."""

import json
import re
from difflib import SequenceMatcher
from collections import defaultdict

import argparse
import sys

def _load_data():
    p = argparse.ArgumentParser(description='Build HomeKit accessory -> HA entity_id map')
    p.add_argument('--export', required=True, help='Merged HomeKit export JSON')
    p.add_argument('--entity-registry', required=True, help='HA core.entity_registry')
    p.add_argument('--device-registry', required=True, help='HA core.device_registry')
    p.add_argument('-o', '--output', default='entity_map.json', help='Output JSON path')
    return p.parse_args()

_args = _load_data()
with open(_args.export, encoding='utf-8') as f:
    merged = json.load(f)
with open(_args.entity_registry, encoding='utf-8') as f:
    ent_reg = json.load(f)
with open(_args.device_registry, encoding='utf-8') as f:
    dev_reg = json.load(f)

devices_by_id = {d["id"]: d for d in dev_reg["data"]["devices"]}
entities = ent_reg["data"]["entities"]

# HomeKit service -> preferred HA domains (ordered by preference)
SVC_DOMAINS = {
    "lightbulb": ["light"],
    "fan": ["fan"], "fanv2": ["fan"],
    "switch": ["switch", "input_boolean"],
    "outlet": ["switch"],
    "lock mechanism": ["lock"],
    "thermostat": ["climate"], "heater-cooler": ["climate"],
    "window covering": ["cover"],
    "garage door opener": ["cover"],
    "valve": ["valve", "switch"],
    "occupancy sensor": ["binary_sensor"],
    "motion sensor": ["binary_sensor"],
    "contact sensor": ["binary_sensor"],
    "leak sensor": ["binary_sensor"],
    "temperature sensor": ["sensor"],
    "humidity sensor": ["sensor"],
    "camera": ["camera"],
    "camera rtp stream management": ["camera"],
    "speaker": ["media_player"],
    "television": ["media_player"],
    "smart speaker": ["media_player"],
    "air purifier": ["fan"],
    "humidifier-dehumidifier": ["humidifier"],
    "stateless programmable switch": ["event"],
}

# Domains that are "utility" and should not be primary matches
UTILITY_DOMAINS = {"update", "button", "number", "select", "text", "diagnostics"}

# Primary controllable domains (in order of preference when no service info)
PRIMARY_DOMAINS = ["light", "switch", "fan", "cover", "lock", "climate",
                   "media_player", "camera", "valve", "humidifier",
                   "binary_sensor", "sensor", "input_boolean", "scene",
                   "automation", "event"]


def normalize(s):
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def name_similarity(a, b):
    """Score how similar two names are. Returns (score, reason)."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0, "EMPTY"
    if na == nb:
        return 1.0, "EXACT"

    # Check if one is contained in the other (with length check)
    if na in nb:
        ratio = len(na) / len(nb)
        if ratio > 0.5 and len(na) >= 5:
            return 0.85 + ratio * 0.10, "CONTAINS"
    if nb in na:
        ratio = len(nb) / len(na)
        if ratio > 0.5 and len(nb) >= 5:
            return 0.85 + ratio * 0.10, "CONTAINS"

    # Check if HK name is a suffix of HA device name (room-prefixed)
    # e.g., HK "Camera Hub G3" vs HA "Living Room Camera Hub G3"
    na_words = na.split()
    nb_words = nb.split()
    if len(na_words) >= 2 and len(nb_words) > len(na_words):
        # Check if HK name appears as suffix of HA name
        suffix_len = len(na_words)
        if nb_words[-suffix_len:] == na_words:
            return 0.92, "ROOM_SUFFIX"
    if len(nb_words) >= 2 and len(na_words) > len(nb_words):
        suffix_len = len(nb_words)
        if na_words[-suffix_len:] == nb_words:
            return 0.92, "ROOM_SUFFIX"

    # Fuzzy
    score = SequenceMatcher(None, na, nb).ratio()
    if score >= 0.70:
        return score, f"FUZZY_{score:.2f}"
    return 0, "NO_MATCH"


def get_preferred_domains(services):
    """Get preferred HA domains from HomeKit service list."""
    domains = []
    for svc in services:
        svc_lower = svc.lower().strip()
        if svc_lower in SVC_DOMAINS:
            domains.extend(SVC_DOMAINS[svc_lower])
    return list(dict.fromkeys(domains))  # dedupe preserving order


# ============ Step 1: Build device -> entities lookup ============
device_entities = defaultdict(list)
standalone_entities = []

for e in entities:
    eid = e.get("entity_id", "")
    if not eid or e.get("disabled_by"):
        continue

    dev_id = e.get("device_id")
    domain = eid.split(".")[0]
    name = e.get("name") or e.get("original_name") or ""
    eid_name = eid.split(".", 1)[1].replace("_", " ")

    entry = {
        "entity_id": eid,
        "domain": domain,
        "name": name,
        "eid_name": eid_name,
        "platform": e.get("platform", ""),
    }

    if dev_id:
        device_entities[dev_id].append(entry)
    else:
        standalone_entities.append(entry)

# Build device name lookup (skip disabled devices and devices with no active entities)
device_names = {}  # device_id -> list of names to match against
for did, dev in devices_by_id.items():
    if dev.get("disabled_by"):
        continue
    if did not in device_entities:
        continue  # no active entities for this device
    names = []
    for n in [dev.get("name_by_user"), dev.get("name")]:
        if n:
            names.append(n)
    if names:
        device_names[did] = names


# ============ Step 2: Gather HK accessories ============
hk_accessories = {}
for acc in merged.get("accessories", []):
    aname = acc.get("name", "")
    if not aname:
        continue
    room = acc.get("room", "")
    services = [s.get("serviceType", "") for s in acc.get("services", [])
                if s.get("serviceType", "") not in
                ("Accessory Information", "Protocol Information", "")]
    hk_accessories[aname] = {"room": room, "services": services}

for auto in merged.get("automations", []):
    for aset in auto.get("actionSets", []):
        for act in aset.get("actions", []):
            aname = act.get("accessoryName", "")
            svc = act.get("serviceName", "")
            if aname and aname not in hk_accessories:
                hk_accessories[aname] = {"room": "", "services": [svc] if svc else []}


# ============ Step 3: Match HK accessories to devices then entities ============

def pick_entity_from_device(dev_id, preferred_domains):
    """Pick best entity from a device given preferred HA domains."""
    ents = device_entities.get(dev_id, [])
    if not ents:
        return None

    # Try preferred domains in order
    for dom in preferred_domains:
        for e in ents:
            if e["domain"] == dom:
                return e["entity_id"]

    # Fall back to primary domain ordering
    for dom in PRIMARY_DOMAINS:
        for e in ents:
            if e["domain"] == dom:
                return e["entity_id"]

    # Last resort: first non-utility entity
    for e in ents:
        if e["domain"] not in UTILITY_DOMAINS:
            return e["entity_id"]

    return ents[0]["entity_id"] if ents else None


def match_to_standalone(hk_name, preferred_domains):
    """Try matching to standalone entities (scenes, input_booleans, etc.)."""
    best_score = 0
    best_eid = None
    best_reason = ""

    for e in standalone_entities:
        for check_name in [e["name"], e["eid_name"]]:
            score, reason = name_similarity(hk_name, check_name)
            if score <= best_score:
                continue
            # Domain bonus
            if preferred_domains and e["domain"] in preferred_domains:
                score += 0.05
            elif e["domain"] in UTILITY_DOMAINS:
                score -= 0.1
            if score > best_score:
                best_score = score
                best_eid = e["entity_id"]
                best_reason = reason

    if best_score >= 0.75:
        return best_eid, best_reason
    return None, "UNMATCHED"


entity_map = {}
match_details = {}

for hk_name in sorted(hk_accessories.keys()):
    hk_info = hk_accessories[hk_name]
    preferred_domains = get_preferred_domains(hk_info.get("services", []))

    # Step 3a: Try matching to a device by name
    best_dev_score = 0
    best_dev_id = None
    best_dev_reason = ""

    # Also try with HK room prefix added/removed
    hk_room = hk_info.get("room", "")
    hk_name_variants = [hk_name]
    if hk_room:
        # Try adding room prefix: "Camera Hub G3" + room "Living Room" -> "Living Room Camera Hub G3"
        hk_name_variants.append(f"{hk_room} {hk_name}")
        # Try removing room prefix if present
        if hk_name.lower().startswith(hk_room.lower()):
            stripped = hk_name[len(hk_room):].strip()
            if stripped:
                hk_name_variants.append(stripped)

    for did, dnames in device_names.items():
        for dname in dnames:
            for variant in hk_name_variants:
                score, reason = name_similarity(variant, dname)
                if score > best_dev_score:
                    best_dev_score = score
                    best_dev_id = did
                    best_dev_reason = reason

    if best_dev_score >= 0.70:
        eid = pick_entity_from_device(best_dev_id, preferred_domains)
        if eid:
            entity_map[hk_name] = eid
            dev = devices_by_id[best_dev_id]
            dname = dev.get("name_by_user") or dev.get("name") or ""
            match_details[hk_name] = f"DEV:{best_dev_reason} ({dname}) -> {eid}"
            continue

    # Step 3b: Try matching entity names directly (for entities with names)
    best_ent_score = 0
    best_ent_id = None
    best_ent_reason = ""

    for did, ents in device_entities.items():
        for e in ents:
            for check_name in filter(None, [e["name"], e["eid_name"]]):
                score, reason = name_similarity(hk_name, check_name)
                # Domain preference
                if preferred_domains and e["domain"] in preferred_domains:
                    score += 0.05
                elif e["domain"] in UTILITY_DOMAINS:
                    score -= 0.15

                if score > best_ent_score:
                    best_ent_score = score
                    best_ent_id = e["entity_id"]
                    best_ent_reason = reason

    if best_ent_score >= 0.75:
        entity_map[hk_name] = best_ent_id
        match_details[hk_name] = f"ENT:{best_ent_reason} -> {best_ent_id}"
        continue

    # Step 3c: Standalone entities
    eid, reason = match_to_standalone(hk_name, preferred_domains)
    if eid:
        entity_map[hk_name] = eid
        match_details[hk_name] = f"STANDALONE:{reason} -> {eid}"
        continue

    # Unmatched
    entity_map[hk_name] = None
    match_details[hk_name] = "UNMATCHED"


# ============ Output ============
matched = sum(1 for v in entity_map.values() if v is not None)
unmatched = sum(1 for v in entity_map.values() if v is None)

print(f"=== ENTITY MAP RESULTS ===")
print(f"  Total HK accessories: {len(hk_accessories)}")
print(f"  Matched:              {matched}")
print(f"  Unmatched/HK-only:    {unmatched}")

# Categorize
dev_matches = sum(1 for d in match_details.values() if d.startswith("DEV:"))
ent_matches = sum(1 for d in match_details.values() if d.startswith("ENT:"))
standalone_matches = sum(1 for d in match_details.values() if d.startswith("STANDALONE:"))
print(f"\n  Device name matches:     {dev_matches}")
print(f"  Entity name matches:     {ent_matches}")
print(f"  Standalone matches:      {standalone_matches}")

print(f"\n=== ALL MAPPINGS ===")
for k in sorted(entity_map.keys()):
    v = entity_map[k]
    detail = match_details.get(k, "")
    if v:
        print(f"  {k:45s} -> {v:50s}  [{detail.split(' -> ')[0]}]")
    else:
        print(f"  {k:45s} -> # HK-ONLY / UNMATCHED")

print(f"\n=== UNMATCHED ===")
for k in sorted(entity_map.keys()):
    if entity_map[k] is None:
        svcs = hk_accessories[k].get("services", [])
        print(f"  {k:45s}  services: {svcs[:5]}")

# Save
output_path = _args.output
export_map = {}
for k, v in sorted(entity_map.items()):
    export_map[k] = v if v else None
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(export_map, f, indent=2, ensure_ascii=False)
print(f"\nSaved to {output_path}")
