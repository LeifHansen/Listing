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

echo "==> Building web bundle"
npm run build

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
