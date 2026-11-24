#!/usr/bin/env node
// Signing for an App Store build made on a CI runner — no Mac, no Xcode
// account, nothing clicked in the developer portal. Used by
// .github/workflows/testflight.yml:
//
//     node scripts/ios-signing.mjs --out "$RUNNER_TEMP/signing"
//
// Talks to the App Store Connect API to find (or, the first time, create)
// the three things a manually signed archive needs, and writes them out:
//
//   distribution.pem         the Apple Distribution certificate
//   key.pem                  its private key (for the .p12 the workflow
//                            builds; the workflow deletes it straight after)
//   profile.mobileprovision  an App Store profile for the app and that
//                            certificate
//   ExportOptions.plist      for `xcodebuild -exportArchive`
//
// and, as step outputs: team_id, app_id, bundle_id, profile_name,
// profile_uuid, issuer_url.
//
//     node scripts/ios-signing.mjs --api-key-only --out <dir>
//
// writes only <dir>/private_keys/AuthKey_<id>.p8, where altool looks for the
// API key, so the upload step can have it on disk for just the upload rather
// than through the build, while the npm packages' own code runs.
//
// The certificate's private key is a repository secret, not made per run.
// That is what makes this repeatable: Apple caps the Apple Distribution
// certificates a team may hold, so a fresh key (and certificate) per build
// would hit the cap within a few builds, the way automatic signing on
// throwaway CI machines does. With one key, the certificate Apple issued for
// it is found again on every run, and when it expires (after a year) the next
// run asks for a new one for the same key, unattended. The profile is reused
// the same way, and replaced when it lapses or no longer names the
// certificate.
//
// Environment (repository secrets; see MOBILE.md, "From GitHub Actions"):
//   APP_STORE_CONNECT_KEY_ID      the API key's 10-character id
//   APP_STORE_CONNECT_ISSUER_ID   the issuer id (a UUID) shown above the keys
//   APP_STORE_CONNECT_KEY         the AuthKey_<id>.p8 file's contents
//   IOS_DISTRIBUTION_KEY          the certificate's RSA private key (PEM)
//   THRYFT_BUNDLE_ID              optional; default: capacitor.config.json appId
//
// The API calls are plain functions over an injected `api`, tested in
// ios-signing.test.mjs against a stand-in for Apple's endpoints.

import { execFileSync } from "node:child_process";
import {
  X509Certificate, createPrivateKey, createPublicKey, sign,
} from "node:crypto";
import {
  appendFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DAY = 24 * 60 * 60 * 1000;

// One profile, found again by name on every run. native-prepare.mjs writes
// this name into the project (THRYFT_IOS_PROFILE) and ExportOptions names it.
export const PROFILE_NAME = "Thryft Shop App Store CI";

// Where Apple's intermediate certificate is, when the certificate doesn't say.
const WWDR_G3 = "https://www.apple.com/certificateauthority/AppleWWDRCAG3.cer";

// ---------------------------------------------------------------------------
// Secrets
// ---------------------------------------------------------------------------

/** A PEM block from a secret, however it was pasted: as the file, as the
 *  file base64-encoded, with Windows line ends, or with literal "\n"s. */
export function pem(text, name) {
  let s = String(text ?? "").replace(/\r\n?/g, "\n").trim();
  if (!s.includes("-----BEGIN")) {
    const decoded = Buffer.from(s.replace(/\s+/g, ""), "base64").toString("utf8").trim();
    if (decoded.includes("-----BEGIN")) s = decoded.replace(/\r\n?/g, "\n");
  }
  if (!s.includes("\n") && s.includes("\\n")) s = s.replace(/\\n/g, "\n");
  if (!/^-----BEGIN [A-Z ]+-----\n[\s\S]+\n-----END [A-Z ]+-----$/.test(s)) {
    throw new Error(`${name} is not a PEM key: paste the whole file, `
      + "its BEGIN and END lines included");
  }
  return `${s}\n`;
}

function distributionKeyOf(text) {
  let key;
  try {
    key = createPrivateKey(text);
  } catch (e) {
    throw new Error(`IOS_DISTRIBUTION_KEY could not be read as a private key (${e.message})`,
      { cause: e });
  }
  if (key.asymmetricKeyType !== "rsa" || key.asymmetricKeyDetails?.modulusLength !== 2048) {
    throw new Error("IOS_DISTRIBUTION_KEY must be a 2048-bit RSA private key: "
      + "Apple issues certificates for no other kind");
  }
  return key;
}

/** The key id and issuer id are easy to paste into each other's secret. */
export function checkIds(keyId, issuerId) {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (uuid.test(keyId) && /^[A-Z0-9]{10}$/.test(issuerId)) {
    throw new Error("APP_STORE_CONNECT_KEY_ID and APP_STORE_CONNECT_ISSUER_ID are swapped");
  }
  if (!/^[A-Z0-9]{10}$/.test(keyId)) {
    throw new Error(`APP_STORE_CONNECT_KEY_ID should be the key's 10-character id, not "${keyId}"`);
  }
  if (!uuid.test(issuerId)) {
    throw new Error("APP_STORE_CONNECT_ISSUER_ID should be the issuer id, a UUID "
      + "(shown above the list of keys)");
  }
}

// ---------------------------------------------------------------------------
// The App Store Connect API
// ---------------------------------------------------------------------------

/** The API's bearer token: an ES256 JWT, good for at most 20 minutes. */
export function ascToken({ keyId, issuerId, key, now = Date.now() }) {
  const enc = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  // A minute's grace for a runner clock a little ahead of Apple's.
  const iat = Math.floor(now / 1000) - 60;
  const data = `${enc({ alg: "ES256", kid: keyId, typ: "JWT" })}.`
    + enc({ iss: issuerId, iat, exp: iat + 15 * 60, aud: "appstoreconnect-v1" });
  const sig = sign("sha256", Buffer.from(data), {
    key: createPrivateKey(key), dsaEncoding: "ieee-p1363",
  });
  return `${data}.${sig.toString("base64url")}`;
}

const HINTS = {
  401: "Apple did not accept the API key. Check that APP_STORE_CONNECT_KEY_ID, "
    + "APP_STORE_CONNECT_ISSUER_ID and APP_STORE_CONNECT_KEY belong together, and "
    + "that it is a Team key, not an Individual one.",
  403: "The API key's role may not do this. Signing creates certificates and "
    + "profiles, which needs a key with Admin access.",
};

export class AppleError extends Error {
  constructor(method, route, status, errors) {
    const said = errors.map((e) => e.detail || e.title).filter(Boolean).join(" / ");
    const hint = HINTS[status] ? `\n   ${HINTS[status]}` : "";
    super(`App Store Connect: ${method} ${route.split("?")[0]} → ${status}: `
      + `${said || "no detail"}${hint}`);
    this.status = status;
  }
}

/** `api(method, route, body)` → the parsed JSON body; throws AppleError. */
export function appStoreConnect({
  keyId, issuerId, key,
  fetch = globalThis.fetch,
  base = "https://api.appstoreconnect.apple.com",
}) {
  return async (method, route, body) => {
    const res = await fetch(base + route, {
      method,
      headers: {
        Authorization: `Bearer ${ascToken({ keyId, issuerId, key })}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 204) return null;
    const text = await res.text();
    let json;
    try {
      json = text ? JSON.parse(text) : {};
    } catch {
      json = {};
    }
    if (!res.ok) throw new AppleError(method, route, res.status, json.errors || []);
    return json;
  };
}

// ---------------------------------------------------------------------------
// Certificate, profile, app
// ---------------------------------------------------------------------------

/** A certificate signing request for the key, as the API takes it (PEM). */
export function csrFor(keyPem) {
  const dir = mkdtempSync(path.join(tmpdir(), "csr-"));
  try {
    const keyFile = path.join(dir, "key.pem");
    writeFileSync(keyFile, keyPem, { mode: 0o600 });
    return execFileSync("openssl",
      ["req", "-new", "-sha256", "-key", keyFile, "-subj", "/CN=Thryft Shop CI/C=US"],
      { encoding: "utf8" });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function certificateOf(resource) {
  const content = resource?.attributes?.certificateContent;
  if (!content) return null;
  const x509 = new X509Certificate(Buffer.from(content, "base64"));
  return {
    id: resource.id,
    name: resource.attributes.name || resource.attributes.displayName || "",
    x509,
    spki: x509.publicKey.export({ type: "spki", format: "der" }),
    expires: Date.parse(x509.validTo),
  };
}

const spkiOf = (key) => createPublicKey(key).export({ type: "spki", format: "der" });

/** Find or make everything the archive and export need. */
export async function prepareSigning({
  api, bundleId, distributionKey,
  now = Date.now(), makeCsr = csrFor, log = console.log,
}) {
  const mine = spkiOf(distributionKeyOf(distributionKey));
  // Anything with less than a day left could lapse mid-build or mid-review.
  const soon = now + DAY;

  // The app record. The upload is addressed to it and only the website can
  // create it, so its absence is the likeliest first-run failure.
  const apps = (await api("GET",
    `/v1/apps?filter[bundleId]=${encodeURIComponent(bundleId)}&limit=200`)).data || [];
  const app = apps.find((a) => a.attributes?.bundleId === bundleId);
  if (!app) {
    throw new Error(`App Store Connect has no app with the bundle id ${bundleId}. `
      + "Create it first: App Store Connect → Apps → + → New App (MOBILE.md, iOS one-time setup).");
  }
  log(`App:          ${app.attributes.name} (Apple ID ${app.id}, ${bundleId})`);

  const bundles = (await api("GET",
    `/v1/bundleIds?filter[identifier]=${encodeURIComponent(bundleId)}&limit=200`)).data || [];
  const bundle = bundles.find((b) => b.attributes?.identifier === bundleId);
  if (!bundle) {
    throw new Error(`The bundle id ${bundleId} is not registered: Certificates, `
      + "Identifiers & Profiles → Identifiers → +.");
  }

  // The certificate Apple issued for this key, if it has a day left.
  const certs = (await api("GET",
    "/v1/certificates?filter[certificateType]=DISTRIBUTION&limit=200")).data || [];
  let cert = certs.map(certificateOf)
    .find((c) => c && c.spki.equals(mine) && c.expires > soon);
  let certMade = false;
  if (!cert) {
    try {
      cert = certificateOf((await api("POST", "/v1/certificates", {
        data: {
          type: "certificates",
          attributes: { certificateType: "DISTRIBUTION", csrContent: makeCsr(distributionKey) },
        },
      })).data);
    } catch (e) {
      if (e instanceof AppleError && e.status === 409) {
        e.message += "\n   If Apple says the team has too many distribution certificates, "
          + "revoke one nobody uses (Certificates, Identifiers & Profiles → Certificates) "
          + "and run this again. Apps already on the App Store are not affected.";
      }
      throw e;
    }
    if (!cert || !cert.spki.equals(mine)) {
      throw new Error("Apple's new certificate is not for IOS_DISTRIBUTION_KEY");
    }
    certMade = true;
  }
  const teamId = cert.x509.subject.match(/^OU=([A-Z0-9]{10})$/m)?.[1];
  if (!teamId) throw new Error(`No team id in the certificate's subject: ${cert.x509.subject}`);
  const issuerUrl = cert.x509.infoAccess?.match(/CA Issuers - URI:(\S+)/)?.[1] || WWDR_G3;
  log(`Certificate:  ${cert.name || "Apple Distribution"} for team ${teamId}, `
    + `until ${new Date(cert.expires).toISOString().slice(0, 10)}`
    + `${certMade ? " (new: Apple emails the account holder about it)" : ""}`);

  // The profile: reused while it is active, names this certificate and has
  // a day left. Any other profile of this name is stale and goes, so the
  // name stays unique.
  const named = ((await api("GET",
    `/v1/bundleIds/${bundle.id}/profiles?limit=200`)).data || [])
    .filter((p) => p.attributes?.name === PROFILE_NAME);
  let profile = null;
  for (const p of named) {
    const a = p.attributes;
    if (!profile && a.profileType === "IOS_APP_STORE" && a.profileState === "ACTIVE"
        && Date.parse(a.expirationDate) > soon && a.profileContent) {
      const ids = ((await api("GET",
        `/v1/profiles/${p.id}/relationships/certificates?limit=200`)).data || [])
        .map((c) => c.id);
      if (ids.includes(cert.id)) {
        profile = p;
        continue;
      }
    }
    await api("DELETE", `/v1/profiles/${p.id}`);
  }
  const profileMade = !profile;
  if (!profile) {
    profile = (await api("POST", "/v1/profiles", {
      data: {
        type: "profiles",
        attributes: { name: PROFILE_NAME, profileType: "IOS_APP_STORE" },
        relationships: {
          bundleId: { data: { type: "bundleIds", id: bundle.id } },
          certificates: { data: [{ type: "certificates", id: cert.id }] },
        },
      },
    })).data;
  }
  const pa = profile.attributes;
  log(`Profile:      ${pa.name} ${pa.uuid}, until ${String(pa.expirationDate).slice(0, 10)}`
    + `${profileMade ? " (new)" : ""}`);

  return {
    appId: app.id,
    bundleId,
    teamId,
    issuerUrl,
    certificatePem: cert.x509.toString(),
    profile: {
      name: pa.name,
      uuid: pa.uuid,
      content: Buffer.from(pa.profileContent, "base64"),
    },
  };
}

const xmlEscape = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Options for `xcodebuild -exportArchive`: an App Store .ipa, signed with
 *  the certificate and profile above, written to disk (the workflow uploads
 *  it with altool, which takes the API key). */
export function exportOptions({ teamId, bundleId, profileName }) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key>
  <string>app-store-connect</string>
  <key>destination</key>
  <string>export</string>
  <key>teamID</key>
  <string>${xmlEscape(teamId)}</string>
  <key>signingStyle</key>
  <string>manual</string>
  <key>signingCertificate</key>
  <string>Apple Distribution</string>
  <key>provisioningProfiles</key>
  <dict>
    <key>${xmlEscape(bundleId)}</key>
    <string>${xmlEscape(profileName)}</string>
  </dict>
  <key>manageAppVersionAndBuildNumber</key>
  <false/>
  <key>uploadSymbols</key>
  <true/>
</dict>
</plist>
`;
}

// ---------------------------------------------------------------------------
// The run
// ---------------------------------------------------------------------------

function secret(name) {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new Error(`${name} is not set: add it under Settings → Secrets and variables → `
      + "Actions (MOBILE.md, \"From GitHub Actions\")");
  }
  return value.trim();
}

export async function main(argv, { fetch = globalThis.fetch } = {}) {
  const at = argv.indexOf("--out");
  const out = path.resolve(at >= 0 && argv[at + 1] ? argv[at + 1] : "signing");

  const keyId = secret("APP_STORE_CONNECT_KEY_ID");
  const issuerId = secret("APP_STORE_CONNECT_ISSUER_ID");
  checkIds(keyId, issuerId);
  const ascKey = pem(secret("APP_STORE_CONNECT_KEY"), "APP_STORE_CONNECT_KEY");
  if (argv.includes("--api-key-only")) {
    mkdirSync(path.join(out, "private_keys"), { recursive: true, mode: 0o700 });
    writeFileSync(path.join(out, "private_keys", `AuthKey_${keyId}.p8`), ascKey, { mode: 0o600 });
    return { out, outputs: {} };
  }
  const distributionKey = pem(secret("IOS_DISTRIBUTION_KEY"), "IOS_DISTRIBUTION_KEY");
  const bundleId = process.env.THRYFT_BUNDLE_ID
    || JSON.parse(readFileSync(path.join(ROOT, "capacitor.config.json"), "utf8")).appId;

  const api = appStoreConnect({ keyId, issuerId, key: ascKey, fetch });
  const s = await prepareSigning({ api, bundleId, distributionKey });

  mkdirSync(out, { recursive: true, mode: 0o700 });
  writeFileSync(path.join(out, "key.pem"), distributionKey, { mode: 0o600 });
  writeFileSync(path.join(out, "distribution.pem"), s.certificatePem);
  writeFileSync(path.join(out, "profile.mobileprovision"), s.profile.content);
  writeFileSync(path.join(out, "ExportOptions.plist"),
    exportOptions({ teamId: s.teamId, bundleId, profileName: s.profile.name }));

  const outputs = {
    team_id: s.teamId,
    app_id: s.appId,
    bundle_id: bundleId,
    profile_name: s.profile.name,
    profile_uuid: s.profile.uuid,
    issuer_url: s.issuerUrl,
  };
  if (process.env.GITHUB_OUTPUT) {
    appendFileSync(process.env.GITHUB_OUTPUT,
      Object.entries(outputs).map(([k, v]) => `${k}=${v}\n`).join(""));
  }
  return { out, outputs };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((e) => {
    console.error(`\n!! ${e.message || e}`);
    process.exit(1);
  });
}
