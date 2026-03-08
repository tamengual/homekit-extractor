# HomeKit Extractor

**Export your entire Apple HomeKit setup — including "Convert to Shortcut" automations — to JSON, with tools to generate a starting point for Home Assistant migration.**

Apple's HomeKit API (`HMHomeManager`) intentionally hides the internals of automations that use the "Convert to Shortcut" feature. This project extracts *everything* — including the conditional logic (if/then/else), scene references, and device actions inside those opaque shortcuts — by combining two extraction methods. It also provides conversion scripts that produce Home Assistant automation YAML as a **starting point that will require manual review and editing** for your specific setup.

## The Problem

If you have a complex HomeKit setup and want to migrate to Home Assistant, you'll quickly discover:

1. **Apple's API hides Shortcut automations.** `HMShortcutAction` objects expose only a name — zero workflow data. No conditionals, no device actions, nothing.
2. **No export feature exists.** Apple provides no way to export automations from the Home app.
3. **Third-party apps hit the same wall.** Controller for HomeKit, Eve, etc. all use the same `HMHomeManager` API and get the same opaque objects.


## The Solution

This project uses **two complementary extraction methods** and a **conversion pipeline**:

### Method 1: Mac Catalyst App (public API)
A SwiftUI app using `HMHomeManager` that exports:
- All accessories with full characteristic metadata (services, values, properties)
- All rooms and zones
- All scenes with their complete action lists
- All automations — triggers, conditions, predicates, and actions
- For shortcut automations: marks them as `isLikelyShortcut: true` (but can't see inside)

### Method 2: homed Database Extraction (the breakthrough)
A Python script that reads Apple's internal HomeKit daemon database (`~/Library/HomeKit/core.sqlite`) and extracts:
- The full Shortcuts workflow definitions (if/then/else blocks, menu branches)
- Device actions inside shortcuts (via protobuf-encoded `HMActionSetSerializedData`)
- Target values (via NSKeyedArchiver-encoded binary plists)
- Trigger events with full characteristic references

### Method 3: Home Assistant Conversion (best-effort)
Python scripts that combine both exports and produce Home Assistant automation YAML **as a starting point**. Every automation gets a readiness classification and `# TODO` annotations where manual work is needed:
- Entity mapping from HomeKit accessory names to HA entity IDs (with ambiguity detection — refuses to guess when multiple candidates are too close)
- Characteristic-to-service-call translation (brightness, color, temperature, etc.)
- Conditional logic preservation (toggle patterns, if/else branches)
- Audit report classifying each automation as `READY_TO_TEST` / `REVIEW_REQUIRED` / `MANUAL_REBUILD` with reason codes
- Structured JSON audit output (`--audit-json`) for tooling and debugging
- Strict mode (`--strict`) that exits nonzero on ambiguous entities or unresolvable automations

## Quick Start

### Prerequisites
- **Mac with HomeKit configured** (the Mac must be on the same iCloud account as your Home setup)
- **Xcode 15+** and **[XcodeGen](https://github.com/yonaskolb/XcodeGen)** (for the Catalyst app)
- **Python 3.9+** (uses only stdlib — no pip packages needed for extraction)
- **Full Disk Access** for Terminal (System Settings > Privacy & Security > Full Disk Access)

### Step 1: Run the Catalyst App
```bash
cd app
xcodegen generate
open HomeKitDumper.xcodeproj
# Build & Run (Cmd+R) — target "My Mac (Designed for iPad)"
# Click "Export HomeKit Data"
# Find homekit_export.json in Documents
```

### Step 2: Extract homed Database
```bash
cd scripts
python3 homed_extract.py -o homekit_homed_export.json
```

### Step 3: Merge Both Exports
```bash
python3 merge_exports.py \
  --app-export ../homekit_export.json \
  --homed-export homekit_homed_export.json \
  -o homekit_merged.json
```

### Step 4: Convert to Home Assistant (Optional — requires manual review)
```bash
# First, get your HA entity registry
# (copy /config/.storage/core.entity_registry from your HA instance)

python3 homekit_to_ha.py \
  --export homekit_merged.json \
  --entity-registry core.entity_registry \
  -o homekit_automations.yaml
```

> **Important:** The converter is **not** a push-button migration tool. It produces
> automation YAML with `# TODO` comments wherever manual intervention is needed —
> entity IDs that couldn't be mapped, button triggers that need device-specific
> configuration, and shortcut actions whose scene references couldn't be resolved.
> The audit report printed at the end classifies each automation as
> `READY_TO_TEST`, `REVIEW_REQUIRED`, or `MANUAL_REBUILD` with reason codes
> explaining *why*.  Even `READY_TO_TEST` automations should be verified — the
> entity mapping is heuristic and may match the wrong device.  Use `--strict` to
> fail the conversion if any entity mapping is ambiguous.  **Do not disable
> HomeKit until you've verified every converted automation works in HA.**
>
> Use `--audit-json audit.json` to get a machine-readable report for scripting
> or building a second-pass fixer.

## Project Structure

```
homekit-extractor/
├── README.md
├── LICENSE                              # MIT
├── .gitignore
├── app/                                 # Mac Catalyst SwiftUI app
│   ├── project.yml                      # XcodeGen config (parameterized)
│   └── HomeKitDumper/
│       ├── HomeKitDumperApp.swift        # @main entry point
│       ├── ContentView.swift            # UI — export button + status
│       ├── HomeKitExporter.swift         # Core export engine (860 lines)
│       ├── SafeKVCReader.m              # ObjC KVC wrapper
│       ├── HomeKitDumper-Bridging-Header.h
│       ├── Info.plist
│       └── HomeKitDumper.entitlements
├── scripts/
│   ├── homed_extract.py                 # Standalone homed DB reader
│   ├── merge_exports.py                 # Combines app + homed outputs
│   ├── homekit_to_ha.py                 # HomeKit → Home Assistant converter
│   └── entity_mapper.py                 # Accessory name → HA entity mapping
├── docs/
│   ├── SCHEMA.md                        # homed database schema reference
│   ├── CONVERSION.md                    # HomeKit → HA conversion guide
│   └── DISCOVERY.md                     # How we discovered the homed approach
└── examples/
    └── sample_output.json               # Sanitized example output
```

## How It Works

### The homed Discovery

Apple's `homed` daemon (launched via `com.apple.homed`) manages all HomeKit data locally in a CoreData SQLite database at `~/Library/HomeKit/core.sqlite`. While the public `HMHomeManager` API intentionally hides Shortcut workflow internals, the database contains everything:

```
ZMKFTRIGGER (automation)
  └─ Z_41TRIGGERS_ (junction) → ZMKFACTIONSET (action group)
                                    └─ ZMKFACTION (individual action)
                                         ├─ Z_ENT=36: CharacteristicWrite → ZTARGETVALUE (bplist)
                                         └─ Z_ENT=40: ShortcutAction → ZDATA (bplist → WFWorkflowActions)
                                              └─ WFWorkflowActions array:
                                                   ├─ conditional (if/else/end)
                                                   ├─ homeaccessory → HMActionSetSerializedData (protobuf)
                                                   └─ choosefrommenu (multi-branch)
```

The Shortcut action data lives in `ZDATA` as a binary plist containing `WFWorkflowActions` — the same format Apple Shortcuts uses. Inside `homeaccessory` actions, device control data is encoded as **protobuf** (`HMActionSetSerializedData`), and target values within that are **NSKeyedArchiver binary plists**. Three layers of encoding, but all decodable with Python stdlib.

See [docs/SCHEMA.md](docs/SCHEMA.md) for the complete database schema and [docs/DISCOVERY.md](docs/DISCOVERY.md) for the full story of how this was found.

## Output Format

The merged JSON output contains every automation with full detail:

```json
{
  "homeName": "My Home",
  "automations": [
    {
      "name": "Bedroom Button Single Press",
      "enabled": true,
      "triggerType": "event",
      "events": [
        {
          "eventType": "charValue",
          "characteristic": "Programmable Switch Event",
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
                {"type": "conditional", "mode": "if", "condition": "is", "inputType": "HomeAccessory"},
                {"type": "homeaccessory", "actionSets": [{"writeActions": [{"enabled": true, "targetValue": false}]}]},
                {"type": "conditional", "mode": "else"},
                {"type": "homeaccessory", "actionSets": [{"writeActions": [{"enabled": true, "targetValue": true}]}]},
                {"type": "conditional", "mode": "end"}
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

## Key Technical Details

### Critical Gotchas (save yourself hours)

1. **`ZACCESSORY1`, not `ZACCESSORY`** — All 1,161 CharacteristicWriteAction rows have NULL in `ZACCESSORY` but valid FKs in `ZACCESSORY1`. CoreData versioning artifact.

2. **Junction table naming** — The trigger↔actionset relationship uses `Z_41TRIGGERS_` with columns `Z_41ACTIONSETS_` and `Z_135TRIGGERS_`. These numbers are CoreData's internal relationship IDs.

3. **Three layers of encoding** for Shortcut device actions:
   - Layer 1: Binary plist (`ZDATA` → `WFWorkflowActions`)
   - Layer 2: Protobuf (`HMActionSetSerializedData`)
   - Layer 3: NSKeyedArchiver bplist (target values within protobuf)

4. **Protobuf UUIDs are isolated** — UUIDs inside `HMActionSetSerializedData` do NOT match any `ZMODELID` or `ZUNIQUEIDENTIFIER` in the database, or `uniqueIdentifier` from the API. Zero overlap across 1,419+ lookups.

5. **API vs Database UUIDs don't match** — The `HMHomeManager` API and homed database use completely different UUID namespaces. Match between exports by **name**, not UUID.

6. **CoreData timestamps** — All timestamps are seconds since 2001-01-01 00:00:00 UTC. Add 978307200 for Unix epoch.

### Shortcut Workflow Action Types

| Identifier | Purpose | Key Parameters |
|-----------|---------|----------------|
| `conditional` | If/else/end blocks | `WFControlFlowMode`: 0=if, 1=else, 2=end |
| `homeaccessory` | Set HomeKit device states | `HMActionSetSerializedData` (protobuf) |
| `choosefrommenu` | Multi-branch menu | `WFControlFlowMode`: 0=start, 1=case, 2=end |

### Common Automation Patterns

| Pattern | Count (typical) | Description |
|---------|----------------|-------------|
| **Toggle** | ~40% | IF light is on → turn off, ELSE → turn on |
| **Direct write** | ~55% | Set characteristics directly (no conditions) |
| **Conditional** | ~5% | Check time/variable, then run scene |
| **Nested IF** | ~3% | Multiple nested conditions |
| **Menu** | ~1% | Multi-branch selection (rare) |

## Limitations

### Extraction
- **Read-only** — This project only reads data. It cannot modify, create, or delete automations.
- **macOS only** — The homed database is only accessible on macOS (not iOS). The Catalyst app runs on both.
- **Full Disk Access required** — The homed database is TCC-protected. You must grant Full Disk Access to Terminal.
- **Protobuf UUIDs unresolvable** — Device references inside shortcut workflows use an isolated UUID namespace. The scripts fall back to name-based matching.
- **No iCloud sync data** — This reads the local database copy. If your Home Hub hasn't synced recently, data may be stale.

### Merge
- **Name-based matching with heuristics** — The merge script uses multi-signal matching (name similarity, trigger type, action count, enabled state) but fundamentally relies on automation names being unique and consistent across both exports. Duplicate names will produce lower-confidence matches flagged with warnings.
- **Protobuf UUIDs don't cross-reference** — UUIDs inside the homed database don't match UUIDs from the API export, so the merge cannot verify identity by UUID.

### Home Assistant Conversion
- **Requires manual review** — The converter generates `# TODO` comments for anything it can't resolve automatically (unmapped entities, device-specific triggers, unresolved scene references). This is a starting point, not a finished product.
- **Entity mapping is fuzzy** — Name-based matching between HomeKit accessory names and HA entity IDs can produce false matches. Always verify the mapped entity IDs.
- **Button triggers are generic** — HomeKit button presses are converted to placeholder event triggers. You'll need to replace these with device triggers specific to your integration (ZHA, MQTT, Matter, etc.).
- **No scene import** — HomeKit scenes are referenced but not converted to HA scenes. You may need to create matching HA scenes manually.

## Privacy & Security

- **Everything runs locally.** No network calls, no telemetry, no data leaves your machine.
- **Read-only access.** The homed database is opened with `?mode=ro` (SQLite read-only URI). The Catalyst app uses standard HomeKit API read methods.
- **No credentials stored.** No API keys, tokens, or passwords involved.
- **Scrub before sharing.** The export contains device names, room names, and automation logic. Review before posting publicly.

## Contributing

Contributions welcome! Areas that could use help:
- **Protobuf UUID resolution** — Finding a way to map HMActionSetSerializedData UUIDs to actual accessories
- **iOS extraction** — Finding an equivalent to the homed database on iOS/iPadOS
- **More workflow action types** — Only `conditional`, `homeaccessory`, and `choosefrommenu` are currently decoded
- **Home Assistant integration** — Improving the entity mapping and conversion logic

## License

MIT — see [LICENSE](LICENSE).

## Doing This Without AI

Every step of this project can be done manually. Here's how:

### Manual Extraction (no AI needed)

1. **Build the Catalyst app yourself:**
   - Open `app/` in Xcode, build for "My Mac (Designed for iPad)"
   - The Swift code is straightforward `HMHomeManager` delegate pattern
   - Click the export button → get `homekit_export.json`

2. **Run the homed extraction script:**
   - Grant Full Disk Access to Terminal
   - Run `python3 scripts/homed_extract.py` — it's pure Python stdlib, no dependencies
   - Outputs a JSON file with all automation data including decoded shortcuts

3. **Merge and inspect manually:**
   - Run `python3 scripts/merge_exports.py` with both JSON files
   - Open the merged JSON in any text editor or `jq`
   - All automation logic is now human-readable JSON

### Manual Home Assistant Conversion (no AI needed)

The conversion scripts automate what you can do by hand:

1. **Map accessories:** Open `homekit_export.json`, find each accessory name, then search your HA entity registry for the matching `entity_id`. The scripts use fuzzy name matching, but you can do exact matches in the HA UI.

2. **Translate characteristics:** HomeKit characteristics map to HA service call parameters:
   | HomeKit Characteristic | HA Service | HA Parameter |
   |----------------------|------------|--------------|
   | Power State (0x25) | `light.turn_on`/`off` | — |
   | Brightness (0x08) | `light.turn_on` | `brightness_pct` |
   | Hue (0x13) | `light.turn_on` | `hs_color[0]` |
   | Saturation (0x2F) | `light.turn_on` | `hs_color[1]` |
   | Color Temperature (0xCE) | `light.turn_on` | `color_temp` |
   | Target Position (0x7C) | `cover.set_cover_position` | `position` |
   | Lock Target State (0x1E) | `lock.lock`/`unlock` | — |

3. **Write automations:** Each HomeKit automation becomes an HA automation YAML block. Toggle patterns become `choose` blocks with state checks. Time triggers become `platform: time`. Button presses become device triggers specific to your integration (ZHA, MQTT, Matter, etc.).

The `scripts/homekit_to_ha.py` script automates all of this, but it's designed to be readable — you can follow the logic and adapt it to your setup.

## How This Project Was Built

This project was built by a human working with two [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (Anthropic's AI coding agent) sessions running simultaneously on different machines:

- **MacBook session (Claude Code):** Built the Mac Catalyst app from a detailed specification prompt, iterated through HomeKit API limitations, discovered that `HMShortcutAction` was opaque, explored dead ends (Controller for HomeKit databases, Apple Shortcuts databases, encrypted keychain stores), then found the `homed` daemon database via `lsof`, reverse-engineered the CoreData schema, decoded the three-layer encoding chain (binary plist → protobuf → NSKeyedArchiver), and wrote the extraction script.

- **Windows PC session (Claude Code):** Received the exported JSON files, analyzed all 207 automations to identify the one causing a real-world problem (lights turning on during a party despite "party mode" being on), mapped 272 HomeKit accessories to Home Assistant entities using fuzzy name matching against the HA entity registry, converted 111 direct-write automations to HA YAML, then used the homed database export to decode and convert the remaining 66 shortcut automations — producing 177 total HA automations with 1,964 service calls.

- **The human** coordinated between the two sessions, transferred files via Google Drive and OneDrive, provided domain knowledge about the physical setup (which buttons are where, what rooms connect, which devices are paired how), and made all architectural decisions.

### If you want to use Claude Code for your own migration:

1. Share the `homekit-dumper-prompt.md` (in `docs/`) with Claude Code on your Mac to build the Catalyst app
2. Run both extraction methods and transfer the JSON files
3. Share the JSON files with Claude Code along with your HA entity registry
4. Ask it to map accessories and convert automations — it can handle the fuzzy matching and YAML generation

### If you want to do it all manually:

Everything above works without AI. The scripts are self-contained Python with no external dependencies. The Catalyst app is a standard Xcode project. The documentation explains every step.

## Acknowledgments

- [Apple's HMCatalog sample code](https://developer.apple.com/documentation/homekit) — Reference for HomeKit API patterns
- [FibaroHomeKitTool](https://github.com/nicorz74/FibaroHomeKitTool) — Inspiration for NSPredicate decomposition
- Built with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) by Anthropic — AI coding agent that handled the reverse engineering, protobuf decoding, and automation conversion
