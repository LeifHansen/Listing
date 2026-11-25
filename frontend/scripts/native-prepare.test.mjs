// The native project edits, run against Capacitor's OWN templates (unpacked
// from the installed CLI) rather than hand-copied fixtures: when a Capacitor
// upgrade reshapes the Xcode or Gradle project, this fails in CI instead of
// the release build failing on the Mac.
//
//     node --test scripts/        (part of `npm test`)

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  IOS_PLIST, addPrivacyManifestToProject, buildNumber, configureGradle,
  configureProject, privacyManifest, setPlistValue,
} from "./native-prepare.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TEMPLATES = path.join(ROOT, "node_modules/@capacitor/cli/assets");

function unpack(tarball) {
  const dir = mkdtempSync(path.join(tmpdir(), "cap-template-"));
  execFileSync("tar", ["xzf", path.join(TEMPLATES, tarball), "-C", dir]);
  return dir;
}
const ios = unpack("ios-spm-template.tar.gz");
const android = unpack("android-template.tar.gz");
const read = (dir, rel) => readFileSync(path.join(dir, rel), "utf8");

const count = (text, needle) => text.split(needle).length - 1;
const balanced = (xml) => count(xml, "<dict>") === count(xml, "</dict>")
  && count(xml, "<array>") === count(xml, "</array>");

test("every permission string lands in the template's Info.plist, once", () => {
  let xml = read(ios, "App/App/Info.plist");
  for (const [key, value] of Object.entries(IOS_PLIST)) xml = setPlistValue(xml, key, value);
  const again = Object.entries(IOS_PLIST).reduce((acc, [k, v]) => setPlistValue(acc, k, v), xml);
  assert.equal(again, xml, "a second run changes nothing");
  for (const key of Object.keys(IOS_PLIST)) assert.equal(count(xml, `<key>${key}</key>`), 1, key);
  assert.match(xml, /<key>ITSAppUsesNonExemptEncryption<\/key>\s*<false\/>/);
  assert.ok(balanced(xml));
  assert.match(xml, /<\/dict>\s*<\/plist>\s*$/);
});

test("a plist value is replaced in place, escaped, and can change type", () => {
  const xml = "<plist><dict>\n\t<key>A</key>\n\t<string>old</string>\n</dict></plist>";
  assert.match(setPlistValue(xml, "A", "x & <y>"), /<key>A<\/key>\n\t<string>x &amp; &lt;y&gt;<\/string>/);
  assert.match(setPlistValue(xml, "A", true), /<key>A<\/key>\n\t<true\/>/);
  assert.equal(count(setPlistValue(xml, "B", "new"), "<key>"), 2);
});

test("the privacy manifest is wired into the App target exactly once", () => {
  const pbx = read(ios, "App/App.xcodeproj/project.pbxproj");
  const wired = addPrivacyManifestToProject(pbx);
  // build file, file reference, group child, resources phase entry
  const lines = wired.split("\n").filter((l) => l.includes("PrivacyInfo.xcprivacy"));
  assert.equal(lines.length, 4);
  assert.match(lines[0], /= \{isa = PBXBuildFile; fileRef = \w{24} \/\* PrivacyInfo\.xcprivacy \*\/; \};$/);
  assert.match(lines[1], /= \{isa = PBXFileReference; .*path = PrivacyInfo\.xcprivacy;/);
  assert.match(lines[2], /^\t+\w{24} \/\* PrivacyInfo\.xcprivacy \*\/,$/);
  assert.match(lines[3], /^\t+\w{24} \/\* PrivacyInfo\.xcprivacy in Resources \*\/,$/);
  assert.equal(addPrivacyManifestToProject(wired), wired, "a second run changes nothing");
  assert.equal(count(wired, "{"), count(wired, "}"));
  assert.equal(count(wired, "("), count(wired, ")"));
});

test("a project that isn't Capacitor's shape is refused, not half-edited", () => {
  assert.throws(() => addPrivacyManifestToProject("// !$*UTF8*$!\n{}"), /template/);
});

test("version, build, device family and team are set in every configuration", () => {
  const pbx = read(ios, "App/App.xcodeproj/project.pbxproj");
  const out = configureProject(pbx, { version: "1.2.0", build: "29911731", team: "ABCDE12345" });
  assert.equal(count(out, "MARKETING_VERSION = 1.2.0;"), count(pbx, "MARKETING_VERSION ="));
  assert.equal(count(out, "CURRENT_PROJECT_VERSION = 29911731;"), count(pbx, "CURRENT_PROJECT_VERSION ="));
  assert.equal(count(out, "TARGETED_DEVICE_FAMILY = 1;"), count(pbx, "TARGETED_DEVICE_FAMILY ="));
  assert.equal(count(out, "DEVELOPMENT_TEAM = ABCDE12345;"), count(pbx, "CODE_SIGN_STYLE = Automatic;"));
  assert.ok(count(pbx, "CODE_SIGN_STYLE = Automatic;") >= 2);

  const moved = configureProject(out, { version: "1.2.0", build: "29911731", team: "ZYXWV98765", devices: "universal" });
  assert.equal(count(moved, "DEVELOPMENT_TEAM ="), count(pbx, "CODE_SIGN_STYLE = Automatic;"));
  assert.ok(!moved.includes("ABCDE12345"));
  assert.ok(moved.includes('TARGETED_DEVICE_FAMILY = "1,2";'));
});

test("with a profile, only the App target's Release is signed manually", () => {
  const pbx = read(ios, "App/App.xcodeproj/project.pbxproj");
  const opts = { version: "1.0.0", build: "1", team: "ABCDE12345", profile: "Thryft Shop App Store CI" };
  const out = configureProject(pbx, opts);
  const automatic = count(pbx, "CODE_SIGN_STYLE = Automatic;");
  assert.equal(count(out, "CODE_SIGN_STYLE = Manual;"), 1);
  assert.equal(count(out, "CODE_SIGN_STYLE = Automatic;"), automatic - 1);
  assert.equal(count(out, 'CODE_SIGN_IDENTITY = "Apple Distribution";'), 1);
  assert.equal(count(out, 'PROVISIONING_PROFILE_SPECIFIER = "Thryft Shop App Store CI";'), 1);
  const release = out.match(/\w{24} \/\* Release \*\/ = \{[\s\S]*?name = Release;/g)
    .find((block) => block.includes("CODE_SIGN_STYLE"));
  assert.match(release, /CODE_SIGN_STYLE = Manual;[\s\S]*PROVISIONING_PROFILE_SPECIFIER/);
  // The project-level identity (Capacitor's "iPhone Developer") is not ours.
  assert.equal(count(out, 'CODE_SIGN_IDENTITY = "iPhone Developer";'),
    count(pbx, 'CODE_SIGN_IDENTITY = "iPhone Developer";'));

  assert.equal(configureProject(out, opts), out, "a second run changes nothing");
  assert.equal(configureProject(out, { ...opts, profile: "" }),
    configureProject(pbx, { ...opts, profile: "" }), "and a run without it is automatic again");
  assert.throws(() => configureProject(pbx, { ...opts, team: "" }), /needs THRYFT_APPLE_TEAM_ID/);
  assert.throws(() => configureProject(pbx, { ...opts, profile: 'x"; FOO = 1' }), /profile name/);
});

test("a malformed team id or device family is refused", () => {
  assert.throws(() => configureProject("", { version: "1", build: "1", team: "abc" }), /team id/);
  assert.throws(() => configureProject("", { version: "1", build: "1", devices: "ipad" }), /THRYFT_IOS_DEVICES/);
});

test("the Android version code and name are set in the template's build.gradle", () => {
  const gradle = read(android, "app/build.gradle");
  const out = configureGradle(gradle, { version: "1.2.0", build: "29911731" });
  assert.match(out, /versionCode 29911731\b/);
  assert.match(out, /versionName "1\.2\.0"/);
  assert.equal(configureGradle(out, { version: "1.2.0", build: "29911731" }), out);
  assert.throws(() => configureGradle("android {}", { version: "1", build: "1" }), /versionCode/);
});

test("the privacy manifest declares the data collected and no API reasons", () => {
  const xml = privacyManifest();
  assert.ok(balanced(xml));
  for (const type of ["EmailAddress", "PhotosorVideos", "UserID", "CrashData"]) {
    assert.match(xml, new RegExp(`NSPrivacyCollectedDataType${type}<`));
  }
  assert.match(xml, /<key>NSPrivacyTracking<\/key>\s*<false\/>/);
  assert.match(xml, /<key>NSPrivacyAccessedAPITypes<\/key>\s*<array\/>/);
  assert.ok(!xml.includes("NSPrivacyCollectedDataTypeTracking</key>\n      <true/>"));
});

test("the build number only goes up, and fits Android's versionCode", () => {
  const now = Date.UTC(2026, 10, 15);
  assert.ok(Number(buildNumber(now + 60000)) > Number(buildNumber(now)));
  assert.ok(Number(buildNumber(Date.UTC(2999, 0, 1))) < 2_100_000_000);
});
