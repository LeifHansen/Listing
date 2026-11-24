#!/usr/bin/env node
// Build the web bundle into the native shell and configure the generated
// project for a store build. One script for both platforms:
//
//     npm run native:ios                  # then archive in Xcode (Mac only)
//     npm run native:android              # then open or build in Android Studio
//     npm run native:android -- --bundle  # ...or build the release AAB here
//
// Why a script at all: frontend/ios and frontend/android are GENERATED (and
// gitignored), so anything typed into Xcode or Android Studio is lost the next
// time they are regenerated. Every setting a store build depends on — the
// permission strings, the privacy manifest, the version and build number, the
// device family, the signing team — is written here instead, on every run.
// A missing iOS permission string doesn't warn: iOS terminates the app the
// moment the feature is touched. Encoding them here makes that impossible to
// forget.
//
// Idempotent: run it before every build. The file edits are plain functions
// (exported, and tested in native-prepare.test.mjs) so they behave the same on
// a Mac and on a Linux CI runner — no BSD-vs-GNU sed, no PlistBuddy.
//
// Settings, all optional:
//   THRYFT_API_BASE            API origin baked into the bundle
//                              (default https://listing-lfwjrg.fly.dev)
//   THRYFT_APP_VERSION         store version (default: package.json "version")
//   THRYFT_BUILD_NUMBER        build number / versionCode (default: minutes
//                              since the epoch — see buildNumber())
//   THRYFT_APPLE_TEAM_ID       10-character Apple team id; sets the signing
//                              team so Xcode needs no clicks before Archive
//   THRYFT_IOS_DEVICES         "iphone" (default) or "universal" (adds iPad)
//   THRYFT_ANDROID_KEYSTORE    upload keystore for --bundle (unsigned without)
//   THRYFT_ANDROID_KEYSTORE_PASSWORD, THRYFT_ANDROID_KEY_ALIAS (default
//   "upload"), THRYFT_ANDROID_KEY_PASSWORD (default: the keystore password)

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// The shell bundles the web app (guideline 4.2 rejects remote-webview apps),
// so the API origin is baked in at build time. The web deploy builds WITHOUT
// VITE_API_BASE and keeps relative URLs; only the native bundle is absolute.
const DEFAULT_API_BASE = "https://listing-lfwjrg.fly.dev";

// Pinned, not "latest": 4.0.0 is a fresh major, and 3.0.5 is what every
// TestFlight build so far was made with. npx'd rather than a dependency so its
// native image toolchain (sharp) stays out of every CI install.
const ASSETS_CLI = "@capacitor/assets@3.0.5";

// Brand canvases from tokens.css, not white: they back Android's adaptive
// icon and the generated splash screens, so white would flash against the
// navy app on every cold start.
const ICON_BG = "#101a2e";
const SPLASH_BG = "#f8f3e7";
const SPLASH_BG_DARK = "#101a2e";

// ---------------------------------------------------------------------------
// iOS
// ---------------------------------------------------------------------------

// The permission prompts iOS shows. Apple rejects strings that don't say WHY,
// so these are specific.
export const IOS_PLIST = {
  NSCameraUsageDescription:
    "Thryft Shop uses your camera to photograph items for your listings.",
  NSPhotoLibraryUsageDescription:
    "Thryft Shop uses your photos to create listings.",
  // The export sheet's "Save Image" writes to the library.
  NSPhotoLibraryAddUsageDescription:
    "Thryft Shop saves processed photos you choose to export.",
  // Required although Shop Mode keeps no audio: iOS routes ANY video capture
  // through the microphone, and without this string tapping "Scan a shelf"
  // kills the app. The shelf scan reads still frames on the phone; a video
  // filmed for a listing keeps its sound, because eBay plays it.
  NSMicrophoneUsageDescription:
    "Thryft Shop uses the microphone when you film a video: a shelf scan, whose sound is discarded on your phone, or a video for your listing.",
  // Answers the export-compliance question App Store Connect otherwise asks
  // on every upload. Exempt: HTTPS only, no custom cryptography in the app.
  ITSAppUsesNonExemptEncryption: false,
};

const xmlEscape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// Set a TOP-LEVEL key of an XML plist to a string or boolean, replacing the
// value if the key is there and adding it before the root dict closes if not.
export function setPlistValue(xml, key, value) {
  const element = typeof value === "boolean"
    ? (value ? "<true/>" : "<false/>")
    : `<string>${xmlEscape(String(value))}</string>`;
  const existing = new RegExp(
    `(<key>${key}</key>\\s*)(<string>[\\s\\S]*?</string>|<true\\s*/>|<false\\s*/>)`);
  if (existing.test(xml)) return xml.replace(existing, (_, lead) => lead + element);
  const close = xml.lastIndexOf("</dict>");
  if (close < 0) throw new Error("Info.plist has no root <dict>");
  return `${xml.slice(0, close)}\t<key>${key}</key>\n\t${element}\n${xml.slice(close)}`;
}

// What the app collects, for App Store Connect's privacy report. Every item
// is linked to the seller's account, used to make the app work, and never
// used for tracking; there are no third-party analytics or ad SDKs. Keep this
// in step with docs/APP_STORE.md ("App Privacy") and the privacy policy.
//   email + display name ... the account (users table)
//   photos or videos ....... item photos and listing videos
//   other user content ..... listing text and the notes typed for the AI
//   emails or text messages  eBay buyer messages, read and answered in-app
//   user id ................ the account id
//   purchase history ....... token purchases (when billing is on)
//   crash data ............. /api/client-errors reports, tagged with the user
const COLLECTED = [
  "EmailAddress", "Name", "PhotosorVideos", "OtherUserContent",
  "EmailsOrTextMessages", "UserID", "PurchaseHistory", "CrashData",
];

// The app-level privacy manifest. It declares no "required reason" APIs: none
// of Capacitor 8, its three plugins, the app's own thryft-cutout plugin
// (frontend/native: Vision, ImageIO and CoreVideo only) or the app template
// calls one (Capacitor ships its own manifest saying so), and a reason
// declared for an API that is never called is a claim nobody can back up.
export function privacyManifest() {
  const collected = COLLECTED.map((type) => `
    <dict>
      <key>NSPrivacyCollectedDataType</key>
      <string>NSPrivacyCollectedDataType${type}</string>
      <key>NSPrivacyCollectedDataTypeLinked</key>
      <true/>
      <key>NSPrivacyCollectedDataTypeTracking</key>
      <false/>
      <key>NSPrivacyCollectedDataTypePurposes</key>
      <array><string>NSPrivacyCollectedDataTypePurposeAppFunctionality</string></array>
    </dict>`).join("");
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSPrivacyTracking</key>
  <false/>
  <key>NSPrivacyTrackingDomains</key>
  <array/>
  <key>NSPrivacyCollectedDataTypes</key>
  <array>${collected}
  </array>
  <key>NSPrivacyAccessedAPITypes</key>
  <array/>
</dict>
</plist>
`;
}

// Fixed object ids for the manifest's entries in project.pbxproj. Any 24 hex
// digits unused in the project will do; fixed ones keep reruns idempotent.
const PRIVACY_REF = "7A11F0C0DE00000000000001";
const PRIVACY_BUILD = "7A11F0C0DE00000000000002";

// Add PrivacyInfo.xcprivacy to the App target's resources. Capacitor's
// template doesn't include it, and a file that is on disk but not in the
// target never reaches the app bundle; this used to be a manual "File → Add
// Files…" step in Xcode. Anchored on the Info.plist reference, which every
// Capacitor template has.
export function addPrivacyManifestToProject(pbx) {
  if (pbx.includes("PrivacyInfo.xcprivacy")) return pbx;
  const info = pbx.match(/\t*(\w{24}) \/\* Info\.plist \*\/,\n/);
  const files = /(\/\* Begin PBXResourcesBuildPhase section \*\/[\s\S]*?files = \(\n)/;
  if (!info || !files.test(pbx)
      || !pbx.includes("/* Begin PBXBuildFile section */\n")
      || !pbx.includes("/* Begin PBXFileReference section */\n")) {
    throw new Error("project.pbxproj is not shaped like Capacitor's template");
  }
  return pbx
    .replace("/* Begin PBXBuildFile section */\n", (m) =>
      `${m}\t\t${PRIVACY_BUILD} /* PrivacyInfo.xcprivacy in Resources */ = {isa = PBXBuildFile; fileRef = ${PRIVACY_REF} /* PrivacyInfo.xcprivacy */; };\n`)
    .replace("/* Begin PBXFileReference section */\n", (m) =>
      `${m}\t\t${PRIVACY_REF} /* PrivacyInfo.xcprivacy */ = {isa = PBXFileReference; lastKnownFileType = text.xml; path = PrivacyInfo.xcprivacy; sourceTree = "<group>"; };\n`)
    .replace(info[0], (m) => `${m}\t\t\t\t${PRIVACY_REF} /* PrivacyInfo.xcprivacy */,\n`)
    .replace(files, (m) => `${m}\t\t\t\t${PRIVACY_BUILD} /* PrivacyInfo.xcprivacy in Resources */,\n`);
}

// Version, build number, device family and (optionally) signing team, set in
// every build configuration. These used to be typed into Xcode by hand before
// each archive, and a forgotten build-number bump is a rejected upload.
export function configureProject(pbx, { version, build, devices = "iphone", team = "" }) {
  if (!["iphone", "universal"].includes(devices)) {
    throw new Error(`THRYFT_IOS_DEVICES must be "iphone" or "universal", not "${devices}"`);
  }
  if (team && !/^[A-Z0-9]{10}$/.test(team)) {
    throw new Error(`THRYFT_APPLE_TEAM_ID must be the 10-character team id, not "${team}"`);
  }
  let out = pbx
    .replace(/MARKETING_VERSION = [^;]*;/g, `MARKETING_VERSION = ${version};`)
    .replace(/CURRENT_PROJECT_VERSION = [^;]*;/g, `CURRENT_PROJECT_VERSION = ${build};`)
    .replace(/TARGETED_DEVICE_FAMILY = [^;]*;/g,
      `TARGETED_DEVICE_FAMILY = ${devices === "universal" ? '"1,2"' : "1"};`);
  if (team) {
    out = out.replace(/\t*DEVELOPMENT_TEAM = [^;]*;\n/g, "")
      .replace(/(\t*)CODE_SIGN_STYLE = Automatic;\n/g,
        (m, tabs) => `${m}${tabs}DEVELOPMENT_TEAM = ${team};\n`);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Android
// ---------------------------------------------------------------------------

export function configureGradle(gradle, { version, build }) {
  if (!/versionCode \d+/.test(gradle) || !/versionName "[^"]*"/.test(gradle)) {
    throw new Error("android/app/build.gradle has no versionCode/versionName to set");
  }
  return gradle
    .replace(/versionCode \d+/, `versionCode ${build}`)
    .replace(/versionName "[^"]*"/, `versionName "${version}"`);
}

// ---------------------------------------------------------------------------
// The run
// ---------------------------------------------------------------------------

// Minutes since the epoch. Both stores refuse an upload whose build number is
// not higher than every earlier one; a commit count is not (a shallow or
// feature-branch checkout counts fewer), a timestamp is, on any machine. It
// also stays under Android's versionCode ceiling (2,100,000,000) for millennia.
export function buildNumber(now = Date.now()) {
  return String(Math.floor(now / 60000));
}

function run(cmd, args, opts = {}) {
  console.log(`$ ${cmd} ${args.join(" ")}`);
  execFileSync(cmd, args, { cwd: ROOT, stdio: "inherit", ...opts });
}

function edit(file, fn) {
  const before = readFileSync(file, "utf8");
  const after = fn(before);
  if (after !== before) writeFileSync(file, after);
}

function gitSha() {
  try {
    return execFileSync("git", ["rev-parse", "--short=12", "HEAD"], { cwd: ROOT }).toString().trim();
  } catch {
    return "";
  }
}

function prepareIos({ version, build }) {
  const plist = path.join(ROOT, "ios/App/App/Info.plist");
  edit(plist, (xml) => Object.entries(IOS_PLIST)
    .reduce((acc, [key, value]) => setPlistValue(acc, key, value), xml));

  writeFileSync(path.join(ROOT, "ios/App/App/PrivacyInfo.xcprivacy"), privacyManifest());
  const pbx = path.join(ROOT, "ios/App/App.xcodeproj/project.pbxproj");
  edit(pbx, (text) => configureProject(addPrivacyManifestToProject(text), {
    version, build,
    devices: process.env.THRYFT_IOS_DEVICES || "iphone",
    team: process.env.THRYFT_APPLE_TEAM_ID || "",
  }));

  console.log(`
==> iOS project ready: version ${version} (${build}).
    npx cap open ios
    ${process.env.THRYFT_APPLE_TEAM_ID ? "" : "Signing & Capabilities → pick your Team (or set THRYFT_APPLE_TEAM_ID),\n    "}destination "Any iOS Device (arm64)" → Product → Archive → Distribute App.`);
}

function bundleAndroid() {
  // Signing goes through AGP's injected-signing properties, passed as
  // ORG_GRADLE_PROJECT_* environment variables so the passwords never appear
  // on a command line (and in the process list), which is how `cap build`
  // would pass them. Without a keystore the bundle is built unsigned: enough
  // to prove the build, not enough for Play.
  const env = { ...process.env };
  const keystore = process.env.THRYFT_ANDROID_KEYSTORE;
  if (keystore) {
    const storePass = process.env.THRYFT_ANDROID_KEYSTORE_PASSWORD;
    if (!storePass) throw new Error("THRYFT_ANDROID_KEYSTORE is set but THRYFT_ANDROID_KEYSTORE_PASSWORD is not");
    const P = "ORG_GRADLE_PROJECT_android.injected.signing.";
    env[`${P}store.file`] = path.resolve(keystore);
    env[`${P}store.password`] = storePass;
    env[`${P}key.alias`] = process.env.THRYFT_ANDROID_KEY_ALIAS || "upload";
    env[`${P}key.password`] = process.env.THRYFT_ANDROID_KEY_PASSWORD || storePass;
  }
  const gradlew = process.platform === "win32" ? "gradlew.bat" : "./gradlew";
  run(gradlew, [":app:bundleRelease", "--no-daemon"], { cwd: path.join(ROOT, "android"), env });
  const aab = path.join(ROOT, "android/app/build/outputs/bundle/release/app-release.aab");
  console.log(`\n==> ${keystore ? "Signed" : "UNSIGNED"} bundle: ${aab}`);
  if (!keystore) console.log("    Set THRYFT_ANDROID_KEYSTORE (+ password) to sign it for Play.");
}

function prepareAndroid({ version, build }, { bundle }) {
  edit(path.join(ROOT, "android/app/build.gradle"), (g) => configureGradle(g, { version, build }));
  if (bundle) {
    bundleAndroid();
  } else {
    console.log(`
==> Android project ready: version ${version} (${build}).
    npx cap open android   → Build → Generate Signed App Bundle
    or: npm run native:android -- --bundle   (see MOBILE.md for signing)`);
  }
}

function main(argv) {
  const platform = argv[0];
  if (!["ios", "android"].includes(platform)) {
    console.error("usage: native-prepare.mjs ios|android [--bundle]");
    process.exit(2);
  }
  const bundle = argv.includes("--bundle");
  if (bundle && platform !== "android") {
    console.error("--bundle is Android-only; iOS archives in Xcode.");
    process.exit(2);
  }

  // A project generated by Capacitor 7 or earlier uses CocoaPods; Capacitor 8
  // generates a Swift Package Manager project, and `cap sync` cannot turn one
  // into the other. The folder is generated and every setting in it is
  // rewritten by this script, so starting over loses nothing.
  if (platform === "ios" && existsSync(path.join(ROOT, "ios/App/Podfile"))) {
    console.error("frontend/ios was generated by an older Capacitor (it has a Podfile).\n"
      + "It is generated and gitignored: delete it and run this again.");
    process.exit(1);
  }

  const version = process.env.THRYFT_APP_VERSION
    || JSON.parse(readFileSync(path.join(ROOT, "package.json"), "utf8")).version;
  const build = process.env.THRYFT_BUILD_NUMBER || buildNumber();
  if (!/^\d+(\.\d+){0,2}$/.test(version)) throw new Error(`version "${version}" is not N[.N[.N]]`);
  if (!/^\d+$/.test(build)) throw new Error(`build number "${build}" is not a whole number`);

  const apiBase = process.env.THRYFT_API_BASE || DEFAULT_API_BASE;
  console.log(`==> Building the web bundle (API at ${apiBase})`);
  run("npm", ["run", "build"], {
    env: { ...process.env, VITE_API_BASE: apiBase, VITE_BUILD_SHA: process.env.VITE_BUILD_SHA || gitSha() },
  });

  if (!existsSync(path.join(ROOT, platform))) run("npx", ["cap", "add", platform]);
  run("npx", ["cap", "sync", platform]);

  // `cap sync` copies web assets and plugins but never makes app icons; this
  // fans frontend/assets out into every size each platform wants. Those
  // sources are generated from vector art (npm run brand:assets) and
  // committed, so there is nothing to redraw here. --assetPath names the
  // FOLDER: a file path is the "No assets found in the asset path" error.
  console.log("==> App icon and splash screens");
  mkdirSync(path.join(ROOT, "assets"), { recursive: true });
  run("npx", ["--yes", ASSETS_CLI, "generate", `--${platform}`,
    "--assetPath", "assets",
    "--iconBackgroundColor", ICON_BG,
    "--splashBackgroundColor", SPLASH_BG,
    "--splashBackgroundColorDark", SPLASH_BG_DARK]);

  if (platform === "ios") prepareIos({ version, build });
  else prepareAndroid({ version, build }, { bundle });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main(process.argv.slice(2));
  } catch (e) {
    console.error(`\n!! ${e.message || e}`);
    process.exit(1);
  }
}
