#!/usr/bin/env python3
"""
merge_exports.py — Combine HMHomeManager API export with homed database export.

The API export (from the Catalyst app) has full characteristic data for non-shortcut
automations, complete scene definitions, and accessory metadata. The homed export has
decoded Shortcut workflow internals that the API cannot access.

This script merges them by matching automations by name, injecting the shortcutWorkflow
data from the homed export into the API export's automation entries.

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
from datetime import datetime


def normalize_name(name):
    """Normalize automation name for fuzzy matching."""
    if not name:
        return ""
    # Remove non-ASCII, lowercase, collapse whitespace
    return " ".join(name.lower().split())


def merge_exports(app_data, homed_data):
    """Merge homed shortcut workflow data into app export automations.

    Strategy:
    1. Match automations by exact name (case-insensitive)
    2. For matched shortcut automations, inject workflowSteps into the app export
    3. Preserve all app export data (it has richer characteristic/scene info)
    4. Add any homed-only automations as new entries
    """
    # Build homed automation lookup by normalized name
    homed_by_name = {}
    for auto in homed_data.get("automations", []):
        name = normalize_name(auto.get("name", ""))
        if name:
            homed_by_name[name] = auto

    stats = {
        "app_automations": len(app_data.get("automations", [])),
        "homed_automations": len(homed_data.get("automations", [])),
        "matched": 0,
        "shortcut_workflows_injected": 0,
        "homed_events_injected": 0,
        "unmatched_app": 0,
        "unmatched_homed": 0,
    }

    matched_homed_names = set()

    # Process each app automation
    for app_auto in app_data.get("automations", []):
        app_name = normalize_name(app_auto.get("name", ""))

        if app_name in homed_by_name:
            homed_auto = homed_by_name[app_name]
            matched_homed_names.add(app_name)
            stats["matched"] += 1

            # Check if homed has shortcut workflow data
            for homed_aset in homed_auto.get("actionSets", []):
                for homed_action in homed_aset.get("actions", []):
                    if homed_action.get("actionType") == "shortcut" and homed_action.get("workflowSteps"):
                        # Find matching action set in app export and inject workflow
                        for app_aset in app_auto.get("actionSets", []):
                            for app_action in app_aset.get("actions", []):
                                if app_action.get("actionType") == "shortcut" or app_auto.get("isLikelyShortcut"):
                                    app_action["workflowSteps"] = homed_action["workflowSteps"]
                                    app_action["actionType"] = "shortcut"
                                    stats["shortcut_workflows_injected"] += 1
                                    break
                            break

            # Inject homed event data if app export is missing it
            if homed_auto.get("events") and not app_auto.get("trigger", {}).get("events"):
                app_auto.setdefault("_homed_events", homed_auto["events"])
                stats["homed_events_injected"] += 1

            # Inject homed trigger type info
            if homed_auto.get("triggerType"):
                app_auto["_homed_triggerType"] = homed_auto["triggerType"]
            if homed_auto.get("significantEvent"):
                app_auto["_homed_significantEvent"] = homed_auto["significantEvent"]
            if homed_auto.get("evaluationCondition"):
                app_auto["_homed_evaluationCondition"] = homed_auto["evaluationCondition"]

    # Check for unmatched
    stats["unmatched_app"] = stats["app_automations"] - stats["matched"]

    # Add any homed-only automations
    homed_only = []
    for auto in homed_data.get("automations", []):
        name = normalize_name(auto.get("name", ""))
        if name and name not in matched_homed_names:
            auto["_source"] = "homed_only"
            homed_only.append(auto)

    stats["unmatched_homed"] = len(homed_only)

    if homed_only:
        app_data.setdefault("automations", []).extend(homed_only)

    # Add merge metadata
    app_data["_merge"] = {
        "mergeDate": datetime.now().isoformat(),
        "appExportSource": "HMHomeManager API (Catalyst app)",
        "homedExportSource": "homed_core.sqlite (daemon database)",
        "stats": stats,
    }

    return app_data, stats


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
        help="Print detailed merge statistics",
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
    print(f"Matched by name:              {stats['matched']}", file=sys.stderr)
    print(f"Shortcut workflows injected:  {stats['shortcut_workflows_injected']}", file=sys.stderr)
    print(f"homed events injected:         {stats['homed_events_injected']}", file=sys.stderr)
    print(f"Unmatched (app only):         {stats['unmatched_app']}", file=sys.stderr)
    print(f"Unmatched (homed only):        {stats['unmatched_homed']}", file=sys.stderr)

    if args.verbose:
        print(f"\nFull stats: {json.dumps(stats, indent=2)}", file=sys.stderr)


if __name__ == "__main__":
    main()
