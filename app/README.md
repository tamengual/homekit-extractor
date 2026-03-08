# HomeKitDumper — Mac Catalyst App

A SwiftUI Mac Catalyst app that exports your entire HomeKit setup to JSON using Apple's `HMHomeManager` API.

## Building

### Prerequisites
- Xcode 15+
- [XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`)
- A Mac on the same iCloud account as your HomeKit setup
- Free Apple Developer account (for code signing)

### Steps

1. Edit `project.yml` and replace `YOUR_TEAM_ID` with your Apple Developer Team ID:
   ```yaml
   DEVELOPMENT_TEAM: YOUR_TEAM_ID  # Find in Xcode > Settings > Accounts
   ```

2. Generate the Xcode project:
   ```bash
   xcodegen generate
   ```

3. Open and build:
   ```bash
   open HomeKitDumper.xcodeproj
   ```
   - Select target: **My Mac (Designed for iPad)**
   - Build & Run (Cmd+R)

4. Grant HomeKit access when prompted

5. Click **"Export HomeKit Data"**

6. Find `homekit_export.json` in your Documents folder

## What It Exports

- **Automations** — All triggers, conditions, predicates, and actions. Shortcut automations are marked as `isLikelyShortcut: true` but their internal workflow is opaque (use `homed_extract.py` for that).
- **Scenes** — All named scenes with their complete action lists
- **Accessories** — All devices with services, characteristics, current values, and metadata
- **Rooms** — All rooms with their assigned accessories

## Source Files

| File | Purpose |
|------|---------|
| `HomeKitDumperApp.swift` | @main entry point |
| `ContentView.swift` | SwiftUI UI — export button + status display |
| `HomeKitExporter.swift` | Core export engine — HMHomeManagerDelegate, automation/scene/accessory extraction, ObjC runtime introspection, NSPredicate decomposition |
| `SafeKVCReader.m` | Objective-C helper for safe Key-Value Coding access |
| `HomeKitDumper-Bridging-Header.h` | ObjC ↔ Swift bridge header |
| `Info.plist` | App configuration, HomeKit usage description |
| `HomeKitDumper.entitlements` | HomeKit entitlement |

## Notes

- The app source code needs to be copied from the MacBook where it was originally developed
- Use Mac Catalyst (not native macOS) — HomeKit framework is only available through Catalyst on Mac
- Development signing works fine, no paid Apple Developer Program needed
- The app is designed to run once, export the JSON, and be done

## Copying Source from MacBook

If you built this project on a different Mac, copy the source files:

```bash
# On the Mac where the app was built:
cp -r ~/Library/CloudStorage/OneDrive-Personal/Documents/Personal/HomeKitDumper/ \
      /path/to/homekit-extractor/app/HomeKitDumper/

# Or via the repo's Google Drive location:
cp -r ~/path/to/HomeKitDumper/ \
      ~/Library/CloudStorage/GoogleDrive-*/My\ Drive/homekit-extractor/app/HomeKitDumper/
```
