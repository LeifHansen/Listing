#!/usr/bin/env bash
#
# Build the web bundle, generate/refresh the iOS project, and write every
# Info.plist key the app needs. Run from a Mac with Xcode:
#
#     cd frontend && ./scripts/ios-prepare.sh
#
# Why this exists: frontend/ios/ is generated (and gitignored), so anything
# hand-edited in Xcode is lost the next time it's regenerated. The permission
# strings used to live only as prose in MOBILE.md, which meant one forgotten
# copy-paste shipped a build that TERMINATES on launch of a camera feature —
# iOS kills the process outright when a protected resource is touched without
# its usage string. Encoding them here makes that impossible to forget.
#
# Idempotent: safe to run before every build.
set -euo pipefail

cd "$(dirname "$0")/.."

PLIST="ios/App/App/Info.plist"
PB=/usr/libexec/PlistBuddy

# The four permission prompts iOS shows, and the text shown in each. Apple
# rejects builds whose strings don't explain the WHY, so these are specific.
#
# NSMicrophoneUsageDescription is required even though we never keep audio:
# "Scan a shelf" (ShopMode) records video via the camera, and iOS treats any
# video capture as microphone access.
set_str() {  # set_str <key> <value>
  $PB -c "Delete :$1" "$PLIST" 2>/dev/null || true
  $PB -c "Add :$1 string $2" "$PLIST"
}

# The shell BUNDLES the web app (guideline 4.2 rejects bare remote-webview
# apps), so the API origin must be baked into the build. The web deploy
# builds withOUT this var and keeps relative URLs; only the native bundle
# gets an absolute base. Override with THRYFT_API_BASE for a staging server.
API_BASE="${THRYFT_API_BASE:-https://listing-lfwjrg.fly.dev}"

echo "==> Building web bundle (API at $API_BASE)"
VITE_API_BASE="$API_BASE" npm run build

if [ ! -d ios ]; then
  echo "==> No ios/ project yet — creating it"
  npx cap add ios
fi

echo "==> Syncing web assets + plugins into ios/"
npx cap sync ios

if [ ! -f "$PLIST" ]; then
  echo "!! $PLIST not found — did 'npx cap add ios' fail?" >&2
  exit 1
fi

echo "==> Writing Info.plist keys"
set_str NSCameraUsageDescription \
  "Thryft Shop uses your camera to photograph items for your listings."
set_str NSPhotoLibraryUsageDescription \
  "Thryft Shop uses your photos to create listings."
set_str NSPhotoLibraryAddUsageDescription \
  "Thryft Shop saves processed photos you choose to export."
set_str NSMicrophoneUsageDescription \
  "Scanning a shelf records a short video, which iOS routes through the microphone. The audio is discarded and never uploaded."

# Permanently answers the export-compliance question App Store Connect asks on
# every single upload. We qualify for the exemption: HTTPS only, no custom
# cryptography in the app.
$PB -c "Delete :ITSAppUsesNonExemptEncryption" "$PLIST" 2>/dev/null || true
$PB -c "Add :ITSAppUsesNonExemptEncryption bool false" "$PLIST"

# --- Privacy manifest (PrivacyInfo.xcprivacy) --------------------------------
# Required since May 2024: App Store Connect rejects binaries that touch
# "required reason" APIs without declaring an approved reason (ITMS-91053).
# Capacitor's runtime touches UserDefaults (CA92.1) and file timestamps
# (C617.1); SystemBootTime (35F9.1) and DiskSpace (E174.1) cover WebKit and
# common plugins. No tracking, no tracking domains.
#
# Recent Capacitor iOS templates ship this file already wired into the Xcode
# target — in that case we overwrite its contents and we're done. If it did
# NOT exist, the file is created but Xcode doesn't know about it yet: add it
# to the App target once (File → Add Files… → tick "App"), and it survives
# every future `cap sync`.
PRIVACY="ios/App/App/PrivacyInfo.xcprivacy"
PRIVACY_EXISTED=1
[ -f "$PRIVACY" ] || PRIVACY_EXISTED=0
echo "==> Writing $PRIVACY"
cat > "$PRIVACY" <<'XCPRIVACY'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSPrivacyTracking</key>
  <false/>
  <key>NSPrivacyTrackingDomains</key>
  <array/>
  <key>NSPrivacyCollectedDataTypes</key>
  <array/>
  <key>NSPrivacyAccessedAPITypes</key>
  <array>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategoryUserDefaults</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array><string>CA92.1</string></array>
    </dict>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategoryFileTimestamp</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array><string>C617.1</string></array>
    </dict>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategorySystemBootTime</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array><string>35F9.1</string></array>
    </dict>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategoryDiskSpace</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array><string>E174.1</string></array>
    </dict>
  </array>
</dict>
</plist>
XCPRIVACY
if [ "$PRIVACY_EXISTED" = 0 ]; then
  echo "    NOTE: PrivacyInfo.xcprivacy was just CREATED. One-time step:"
  echo "    open Xcode → File → Add Files to \"App\"… → select it → tick the"
  echo "    App target. (Newer Capacitor templates include it already.)"
fi

echo "==> Verifying"
for key in NSCameraUsageDescription NSPhotoLibraryUsageDescription \
           NSPhotoLibraryAddUsageDescription NSMicrophoneUsageDescription \
           ITSAppUsesNonExemptEncryption; do
  printf '    %-36s %s\n' "$key" "$($PB -c "Print :$key" "$PLIST")"
done

cat <<'DONE'

==> Ready. Next:
      npx cap open ios
    Then in Xcode: Signing & Capabilities → your Team;
    General → set Version / bump Build;
    destination "Any iOS Device (arm64)" → Product → Archive → Distribute.
DONE
