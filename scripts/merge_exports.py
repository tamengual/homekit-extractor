#!/usr/bin/env python3
"""
merge_exports.py — Combine HMHomeManager API export with homed database export.

The API export (from the Catalyst app) has full characteristic data for non-shortcut
automations, complete scene definitions, and accessory metadata. The homed export has
decoded Shortcut workflow internals that the API cannot access.

This script merges them using multi-signal matching (name + trigger type + action
count + enabled state) to pair automations across exports, then produces a single
canonical merged schema — no sidecar fields.

Usage:
    python3 merge_exports.py \
        --app-export homekit_export.json \
        --homed-export homekit_homed_export.json \
        -o homekit_merged.json

Requirements:
    Python 3.9+ (stdlib only)
"""

import json
import argparse
import sys
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher


# ============ NAME NORMALIZATION ============

def normalize_name(name):
    """Normalize automation name for fuzzy matching.

    Lowercases, strips non-ASCII characters, and collapses whitespace
    so that trivial formatting differences don't prevent matches.
    """
    if not name:
        return ""
    # Replace non-ASCII whitespace-like chars with spaces, strip remaining
    # non-ASCII, then lowercase and collapse whitespace.
    cleaned = ""
    for ch in name:
        if ord(ch) > 127:
            # Treat non-ASCII whitespace (e.g., narrow no-break space) as space
            if unicodedata.category(ch).startswith("Z"):
                cleaned += " "
            # Drop other non-ASCII characters
        else:
            cleaned += ch
    return " ".join(cleaned.lower().split())


# ============ MULTI-SIGNAL MATCHING ============

def compute_match_score(app_auto, homed_auto):
    """Compute a confidence score (0.0–1.0) for how likely two automations
    are the same, using multiple signals beyond just the name.

    Signals used:
    - Name similarity (SequenceMatcher ratio) — weighted 0.50
    - Trigger type match — weighted 0.20
    - Enabled state match — weighted 0.10
    - Action count proximity — weighted 0.10
    - Event count proximity — weighted 0.10
    """
    score = 0.0

    # Signal 1: Name similarity (0.0–0.50)
    app_name = normalize_name(app_auto.get("name", ""))
    homed_name = normalize_name(homed_auto.get("name", ""))
    if app_name and homed_name:
        if app_name == homed_name:
            score += 0.50
        else:
            ratio = SequenceMatcher(None, app_name, homed_name).ratio()
            score += 0.50 * ratio

    # Signal 2: Trigger type match (0.0 or 0.20)
    app_trigger = app_auto.get("triggerType", "")
    homed_trigger = homed_auto.get("triggerType", "")
    if app_trigger and homed_trigger:
        if app_trigger == homed_trigger:
            score += 0.20
    elif not app_trigger and not homed_trigger:
        score += 0.10  # Both missing — weak signal

    # Signal 3: Enabled state (0.0 or 0.10)
    app_enabled = app_auto.get("enabled")
    homed_enabled = homed_auto.get("enabled")
    if app_enabled is not None and homed_enabled is not None:
        if app_enabled == homed_enabled:
            score += 0.10

    # Signal 4: Action count proximity (0.0–0.10)
    app_action_count = sum(
        len(aset.get("actions", []))
        for aset in app_auto.get("actionSets", [])
    )
    homed_action_count = sum(
        len(aset.get("actions", []))
        for aset in homed_auto.get("actionSets", [])
    )
    if app_action_count > 0 or homed_action_count > 0:
        max_count = max(app_action_count, homed_action_count, 1)
        diff = abs(app_action_count - homed_action_count)
        score += 0.10 * max(0, 1.0 - diff / max_count)

    # Signal 5: Event count proximity (0.0–0.10)
    app_events = len(app_auto.get("events", []))
    # App export may nest events under trigger.events
    if not app_events:
        app_events = len(app_auto.get("trigger", {}).get("events", []))
    homed_events = len(homed_auto.get("events", []))
    if app_events > 0 or homed_events > 0:
        max_events = max(app_events, homed_events, 1)
        diff = abs(app_events - homed_events)
        score += 0.10 * max(0, 1.0 - diff / max_events)

    return score


def find_best_matches(app_automations, homed_automations, min_score=0.45):
    """Match app automations to homed automations using multi-signal scoring.

    Uses a greedy approach: for each app automation, finds the highest-scoring
    homed match above min_score.  Each homed automation can only be claimed once.

    Returns:
        matches: list of (app_idx, homed_idx, score) tuples
        unmatched_app: list of app indices with no match
        unmatched_homed: list of homed indices with no match
    """
    # Pre-compute normalized name index for fast candidate filtering
    homed_by_name = {}
    for i, auto in enumerate(homed_automations):
        name = normalize_name(auto.get("name", ""))
        if name:
            homed_by_name.setdefault(name, []).append(i)

    # Score all plausible pairs
    candidates = []
    for app_idx, app_auto in enumerate(app_automations):
        app_name = normalize_name(app_auto.get("name", ""))

        # Fast path: only score against name-similar homed automations
        homed_candidates = set()
        if app_name in homed_by_name:
            homed_candidates.update(homed_by_name[app_name])

        # Also check fuzzy name matches (ratio > 0.6)
        for homed_name, indices in homed_by_name.items():
            if homed_name != app_name:
                ratio = SequenceMatcher(None, app_name, homed_name).ratio()
                if ratio > 0.6:
                    homed_candidates.update(indices)

        for homed_idx in homed_candidates:
            score = compute_match_score(app_auto, homed_automations[homed_idx])
            if score >= min_score:
                candidates.append((app_idx, homed_idx, score))

    # Greedy assignment: highest scores first, each side claimed at most once
    candidates.sort(key=lambda x: x[2], reverse=True)
    matched_app = set()
    matched_homed = set()
    matches = []

    for app_idx, homed_idx, score in candidates:
        if app_idx not in matched_app and homed_idx not in matched_homed:
            matches.append((app_idx, homed_idx, score))
            matched_app.add(app_idx)
            matched_homed.add(homed_idx)

    unmatched_app = [i for i in range(len(app_automations)) if i not in matched_app]
    unmatched_homed = [i for i in range(len(homed_automations)) if i not in matched_homed]

    return matches, unmatched_app, unmatched_homed


# ============ CANONICAL MERGE ============

def merge_into_canonical(app_auto, homed_auto):
    """Merge a matched pair into a single canonical automation record.

    The app export is the base (richer characteristic/scene data).
    The homed export contributes:
    - workflowSteps (shortcut internals)
    - events (if app export is missing trigger event details)
    - triggerType, significantEvent, evaluationCondition (if missing)

    All fields are promoted to top-level canonical keys — no _homed_* sidecar.
    """
    merged = dict(app_auto)  # Start with app data

    # Inject shortcut workflow steps
    for homed_aset in homed_auto.get("actionSets", []):
        for homed_action in homed_aset.get("actions", []):
            if homed_action.get("actionType") == "shortcut" and homed_action.get("workflowSteps"):
                for app_aset in merged.get("actionSets", []):
                    for app_action in app_aset.get("actions", []):
                        if app_action.get("actionType") == "shortcut" or merged.get("isLikelyShortcut"):
                            app_action["workflowSteps"] = homed_action["workflowSteps"]
                            app_action["actionType"] = "shortcut"
                            break
                    break

    # Promote trigger event data (canonical, not sidecar)
    if homed_auto.get("events") and not merged.get("events"):
        # Check nested locations too
        has_events = bool(merged.get("trigger", {}).get("events"))
        if not has_events:
            merged["events"] = homed_auto["events"]

    # Promote trigger metadata (canonical fields)
    if homed_auto.get("triggerType") and not merged.get("triggerType"):
        merged["triggerType"] = homed_auto["triggerType"]
    if homed_auto.get("significantEvent") and not merged.get("significantEvent"):
        merged["significantEvent"] = homed_auto["significantEvent"]
    if homed_auto.get("significantEventOffsetSeconds") and not merged.get("significantEventOffsetSeconds"):
        merged["significantEventOffsetSeconds"] = homed_auto["significantEventOffsetSeconds"]
    if homed_auto.get("evaluationCondition") and not merged.get("evaluationCondition"):
        merged["evaluationCondition"] = homed_auto["evaluationCondition"]

    return merged


def merge_exports(app_data, homed_data):
    """Merge homed shortcut workflow data into app export automations.

    Strategy:
    1. Multi-signal matching (name + trigger type + action count + enabled state)
    2. For matched automations, merge into canonical schema (no _homed_* sidecar)
    3. Preserve all app export data (it has richer characteristic/scene info)
    4. Add any homed-only automations as new entries with source annotation
    5. Report match confidence so users can review low-confidence matches
    """
    app_autos = app_data.get("automations", [])
    homed_autos = homed_data.get("automations", [])

    matches, unmatched_app_idxs, unmatched_homed_idxs = find_best_matches(
        app_autos, homed_autos
    )

    stats = {
        "app_automations": len(app_autos),
        "homed_automations": len(homed_autos),
        "matched": len(matches),
        "shortcut_workflows_injected": 0,
        "unmatched_app": len(unmatched_app_idxs),
        "unmatched_homed": len(unmatched_homed_idxs),
        "match_details": [],
    }

    # Apply merges
    for app_idx, homed_idx, score in matches:
        app_auto = app_autos[app_idx]
        homed_auto = homed_autos[homed_idx]

        had_workflow = any(
            action.get("workflowSteps")
            for aset in homed_auto.get("actionSets", [])
            for action in aset.get("actions", [])
            if action.get("actionType") == "shortcut"
        )

        merged = merge_into_canonical(app_auto, homed_auto)
        merged["_matchConfidence"] = round(score, 3)
        app_autos[app_idx] = merged

        if had_workflow:
            stats["shortcut_workflows_injected"] += 1

        stats["match_details"].append({
            "name": app_auto.get("name", ""),
            "confidence": round(score, 3),
        })

    # Add homed-only automations
    homed_only = []
    for idx in unmatched_homed_idxs:
        auto = dict(homed_autos[idx])
        auto["_source"] = "homed_only"
        homed_only.append(auto)

    if homed_only:
        app_data.setdefault("automations", []).extend(homed_only)

    # Add merge metadata
    app_data["_merge"] = {
        "mergeDate": datetime.now().isoformat(),
        "appExportSource": "HMHomeManager API (Catalyst app)",
        "homedExportSource": "homed_core.sqlite (daemon database)",
        "matchStrategy": "multi-signal (name + triggerType + enabled + actionCount + eventCount)",
        "minMatchScore": 0.45,
        "stats": stats,
    }

    return app_data, stats


# ============ CLI ============

def main():
    parser = argparse.ArgumentParser(
        description="Merge HomeKit API export with homed database export",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 merge_exports.py --app-export app.json --homed-export homed.json -o merged.json
  python3 merge_exports.py --app-export app.json --homed-export homed.json  # prints to stdout
        """,
    )
    parser.add_argument(
        "--app-export",
        required=True,
        help="Path to HMHomeManager API export JSON (from Catalyst app)",
    )
    parser.add_argument(
        "--homed-export",
        required=True,
        help="Path to homed database export JSON (from homed_extract.py)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed merge statistics including per-automation confidence",
    )
    args = parser.parse_args()

    # Load exports
    try:
        with open(args.app_export, "r") as f:
            app_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: App export not found: {args.app_export}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in app export: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.homed_export, "r") as f:
            homed_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: homed export not found: {args.homed_export}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in homed export: {e}", file=sys.stderr)
        sys.exit(1)

    # Merge
    merged, stats = merge_exports(app_data, homed_data)

    # Output
    output_json = json.dumps(merged, indent=2, default=str, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"Merged export written to: {args.output}", file=sys.stderr)
    else:
        print(output_json)

    # Print stats
    print(f"\n--- Merge Statistics ---", file=sys.stderr)
    print(f"App export automations:       {stats['app_automations']}", file=sys.stderr)
    print(f"homed export automations:      {stats['homed_automations']}", file=sys.stderr)
    print(f"Matched (multi-signal):       {stats['matched']}", file=sys.stderr)
    print(f"Shortcut workflows injected:  {stats['shortcut_workflows_injected']}", file=sys.stderr)
    print(f"Unmatched (app only):         {stats['unmatched_app']}", file=sys.stderr)
    print(f"Unmatched (homed only):        {stats['unmatched_homed']}", file=sys.stderr)

    if args.verbose and stats.get("match_details"):
        print(f"\n--- Match Details ---", file=sys.stderr)
        # Sort by confidence ascending to highlight weak matches
        for detail in sorted(stats["match_details"], key=lambda d: d["confidence"]):
            conf = detail["confidence"]
            flag = " ⚠️" if conf < 0.70 else ""
            print(f"  {conf:.3f}  {detail['name']}{flag}", file=sys.stderr)


if __name__ == "__main__":
    main()
