// The TestFlight workflow's signing, against a stand-in for the App Store
// Connect API: its endpoints answer from memory, and a certificate request is
// "issued" by openssl, so every certificate here really is for the key whose
// CSR asked for it — which is the property the script relies on.
//
//     node --test scripts/        (part of `npm test`)

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { generateKeyPairSync, createPublicKey, verify } from "node:crypto";
import {
  mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  PROFILE_NAME, appStoreConnect, ascToken, checkIds, csrFor, exportOptions, main,
  pem, prepareSigning,
} from "./ios-signing.mjs";

const BUNDLE_ID = "com.thryftshop.app";
const TEAM = "ABCDE12345";
const DAY = 24 * 60 * 60 * 1000;
const work = mkdtempSync(path.join(tmpdir(), "ios-signing-"));

const rsa = () => generateKeyPairSync("rsa", { modulusLength: 2048 })
  .privateKey.export({ type: "pkcs8", format: "pem" });
const MY_KEY = rsa();
const OTHER_KEY = rsa();
const ASC_KEY = generateKeyPairSync("ec", { namedCurve: "P-256" })
  .privateKey.export({ type: "pkcs8", format: "pem" });

// A stand-in WWDR: the issuer of every certificate the fake hands out.
const caKey = path.join(work, "ca.key");
const caCert = path.join(work, "ca.pem");
execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", caKey,
  "-subj", "/CN=Fake WWDR", "-days", "30", "-out", caCert], { stdio: "ignore" });

let serial = 0;
function issue(csr) {
  const csrFile = path.join(work, `req-${++serial}.csr`);
  writeFileSync(csrFile, csr);
  const pemText = execFileSync("openssl", ["req", "-x509", "-in", csrFile,
    "-CA", caCert, "-CAkey", caKey, "-days", "365", "-set_serial", String(serial),
    "-subj", `/CN=Apple Distribution: Test Co (${TEAM})/OU=${TEAM}/O=Test Co/C=US`,
    "-addext", "authorityInfoAccess=caIssuers;URI:http://certs.apple.com/wwdrg3.der"],
  { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  const der = Buffer.from(pemText.replace(/-----[^-]+-----|\s/g, ""), "base64");
  return { id: `CERT${serial}`, attributes: {
    name: "Apple Distribution: Test Co", certificateType: "DISTRIBUTION",
    certificateContent: der.toString("base64"),
  } };
}

/** The endpoints prepareSigning uses, answering from `state`. */
function fakeApple(state) {
  const calls = [];
  const fetch = async (url, init) => {
    const u = new URL(url);
    const route = u.pathname;
    const { method } = init;
    calls.push(`${method} ${route}`);
    const answer = (status, body) => new Response(
      body === undefined ? null : JSON.stringify(body), { status });
    assert.match(init.headers.Authorization, /^Bearer [\w-]+\.[\w-]+\.[\w-]+$/);
    const body = init.body && JSON.parse(init.body);

    if (method === "GET" && route === "/v1/apps") {
      const id = u.searchParams.get("filter[bundleId]");
      return answer(200, { data: state.apps.filter((a) => a.attributes.bundleId.startsWith(id)) });
    }
    if (method === "GET" && route === "/v1/bundleIds") {
      const id = u.searchParams.get("filter[identifier]");
      return answer(200, { data: state.bundles.filter((b) => b.attributes.identifier.startsWith(id)) });
    }
    if (method === "GET" && route === "/v1/certificates") {
      assert.equal(u.searchParams.get("filter[certificateType]"), "DISTRIBUTION");
      return answer(200, { data: state.certs });
    }
    if (method === "POST" && route === "/v1/certificates") {
      assert.equal(body.data.attributes.certificateType, "DISTRIBUTION");
      const cert = issue(body.data.attributes.csrContent);
      state.certs.push(cert);
      return answer(201, { data: cert });
    }
    let m;
    if (method === "GET" && (m = route.match(/^\/v1\/bundleIds\/(\w+)\/profiles$/))) {
      return answer(200, { data: state.profiles.filter((p) => p.bundle === m[1]) });
    }
    if (method === "GET" && (m = route.match(/^\/v1\/profiles\/(\w+)\/relationships\/certificates$/))) {
      const p = state.profiles.find((x) => x.id === m[1]);
      return answer(200, { data: p.certIds.map((id) => ({ type: "certificates", id })) });
    }
    if (method === "DELETE" && (m = route.match(/^\/v1\/profiles\/(\w+)$/))) {
      state.profiles = state.profiles.filter((p) => p.id !== m[1]);
      return answer(204);
    }
    if (method === "POST" && route === "/v1/profiles") {
      const { attributes, relationships } = body.data;
      if (state.profiles.some((p) => p.attributes.name === attributes.name)) {
        return answer(409, { errors: [{ detail: "Multiple profiles found with the name" }] });
      }
      const p = profile({
        name: attributes.name, type: attributes.profileType,
        bundle: relationships.bundleId.data.id,
        certIds: relationships.certificates.data.map((c) => c.id),
        expires: state.now + 365 * DAY,
      });
      state.profiles.push(p);
      return answer(201, { data: p });
    }
    return answer(404, { errors: [{ detail: `no fake for ${method} ${route}` }] });
  };
  return { fetch, calls };
}

let profiles = 0;
function profile({ name = PROFILE_NAME, type = "IOS_APP_STORE", state = "ACTIVE",
  bundle = "B1", certIds, expires }) {
  const n = ++profiles;
  return {
    id: `PROF${n}`, bundle, certIds,
    attributes: {
      name, profileType: type, profileState: state, uuid: `uuid-${n}`,
      expirationDate: new Date(expires).toISOString(),
      profileContent: Buffer.from(`profile ${n}`).toString("base64"),
    },
  };
}

function world({ now = Date.now(), ...rest } = {}) {
  return {
    now,
    apps: [{ id: "6700000001", attributes: { name: "Thryft Shop", bundleId: BUNDLE_ID } }],
    bundles: [
      { id: "B0", attributes: { identifier: `${BUNDLE_ID}.widget` } },
      { id: "B1", attributes: { identifier: BUNDLE_ID } },
    ],
    certs: [],
    profiles: [],
    ...rest,
  };
}

function run(state, opts = {}) {
  const apple = fakeApple(state);
  const api = appStoreConnect({ keyId: "KEY1234567", issuerId: "x", key: ASC_KEY, fetch: apple.fetch });
  return prepareSigning({
    api, bundleId: BUNDLE_ID, distributionKey: MY_KEY, now: state.now, log: () => {}, ...opts,
  }).then((result) => ({ result, calls: apple.calls }));
}

const spki = (key) => createPublicKey(key).export({ type: "spki", format: "der" });
const writes = (calls) => calls.filter((c) => !c.startsWith("GET"));

test("the API token is an ES256 JWT that verifies with the key's public half", () => {
  const now = Date.UTC(2026, 10, 23, 12);
  const jwt = ascToken({ keyId: "KEY1234567", issuerId: "issuer-1", key: ASC_KEY, now });
  const [h, p, s] = jwt.split(".");
  const header = JSON.parse(Buffer.from(h, "base64url"));
  const payload = JSON.parse(Buffer.from(p, "base64url"));
  assert.deepEqual(header, { alg: "ES256", kid: "KEY1234567", typ: "JWT" });
  assert.equal(payload.iss, "issuer-1");
  assert.equal(payload.aud, "appstoreconnect-v1");
  assert.ok(payload.iat <= now / 1000 && payload.exp > now / 1000);
  assert.ok(payload.exp - payload.iat <= 20 * 60, "Apple refuses tokens over 20 minutes");
  assert.ok(verify("sha256", Buffer.from(`${h}.${p}`),
    { key: createPublicKey(ASC_KEY), dsaEncoding: "ieee-p1363" }, Buffer.from(s, "base64url")));
});

test("first run: a certificate for the secret key, then a profile naming it", async () => {
  // Someone's certificate from Xcode, and a profile of our name left over
  // from it: neither is ours to use, and the stale profile must go.
  const theirs = issue(csrFor(OTHER_KEY));
  const state = world({ certs: [theirs] });
  state.profiles.push(profile({ certIds: [theirs.id], expires: state.now + 100 * DAY }));
  const { result, calls } = await run(state);

  assert.deepEqual(writes(calls), ["POST /v1/certificates", "DELETE /v1/profiles/PROF1", "POST /v1/profiles"]);
  const mine = state.certs.at(-1);
  assert.ok(Buffer.from(mine.attributes.certificateContent, "base64").length > 0);
  assert.equal(state.profiles.length, 1);
  assert.deepEqual(state.profiles[0].certIds, [mine.id]);
  assert.equal(state.profiles[0].bundle, "B1", "the app's bundle id, not the prefix match");

  assert.equal(result.teamId, TEAM);
  assert.equal(result.appId, "6700000001");
  assert.equal(result.issuerUrl, "http://certs.apple.com/wwdrg3.der");
  assert.equal(result.profile.name, PROFILE_NAME);
  assert.equal(result.profile.content.toString(), `profile ${profiles}`);
  const cert = createPublicKey(result.certificatePem).export({ type: "spki", format: "der" });
  assert.ok(cert.equals(spki(MY_KEY)), "the certificate is for IOS_DISTRIBUTION_KEY");
});

test("later runs find both again and change nothing", async () => {
  const state = world();
  const first = await run(state);
  const again = await run(state);
  assert.deepEqual(writes(again.calls), []);
  assert.equal(again.result.profile.uuid, first.result.profile.uuid);
  assert.equal(state.certs.length, 1, "no certificate per build: Apple caps them");
});

test("a lapsing certificate is renewed for the same key, and the profile with it", async () => {
  const state = world();
  const first = await run(state);
  const stale = state.profiles[0].id;
  state.now += 365 * DAY; // the certificate (and profile) now have under a day left
  const later = await run(state);
  assert.deepEqual(writes(later.calls),
    ["POST /v1/certificates", `DELETE /v1/profiles/${stale}`, "POST /v1/profiles"]);
  assert.equal(state.certs.length, 2);
  assert.equal(state.profiles.length, 1);
  assert.deepEqual(state.profiles[0].certIds, [state.certs[1].id]);
  assert.notEqual(later.result.profile.uuid, first.result.profile.uuid);
  assert.ok(createPublicKey(later.result.certificatePem)
    .export({ type: "spki", format: "der" }).equals(spki(MY_KEY)));
});

test("a profile that is invalid, or lists another certificate, is replaced", async () => {
  const state = world();
  await run(state);
  const [invalid] = state.profiles;
  invalid.attributes.profileState = "INVALID";
  let { calls } = await run(state);
  assert.deepEqual(writes(calls), [`DELETE /v1/profiles/${invalid.id}`, "POST /v1/profiles"]);
  assert.equal(state.profiles.length, 1);
  assert.equal(state.profiles[0].attributes.profileState, "ACTIVE");

  const [elsewhere] = state.profiles;
  elsewhere.certIds = ["SOMEONE-ELSES"];
  ({ calls } = await run(state));
  assert.deepEqual(writes(calls), [`DELETE /v1/profiles/${elsewhere.id}`, "POST /v1/profiles"]);
});

test("with no app record yet, it says how to make one, and creates nothing", async () => {
  const state = world({ apps: [] });
  const apple = fakeApple(state);
  const api = appStoreConnect({ keyId: "KEY1234567", issuerId: "x", key: ASC_KEY, fetch: apple.fetch });
  await assert.rejects(
    prepareSigning({ api, bundleId: BUNDLE_ID, distributionKey: MY_KEY, log: () => {} }),
    /no app with the bundle id com\.thryftshop\.app[\s\S]*New App/);
  assert.deepEqual(writes(apple.calls), []);
});

test("Apple's refusals say which secret or role to fix", async () => {
  const refuse = (status, detail) => appStoreConnect({
    keyId: "KEY1234567", issuerId: "x", key: ASC_KEY,
    fetch: async () => new Response(JSON.stringify({ errors: [{ detail }] }), { status }),
  });
  await assert.rejects(refuse(401, "Authentication credentials are missing or invalid.")("GET", "/v1/apps?x=1"),
    (e) => e.status === 401 && /GET \/v1\/apps → 401/.test(e.message) && /Team key/.test(e.message));
  await assert.rejects(refuse(403, "not allowed")("POST", "/v1/certificates", {}),
    /Admin access/);
  const capped = fakeApple(world());
  const api = appStoreConnect({ keyId: "KEY1234567", issuerId: "x", key: ASC_KEY,
    fetch: async (url, init) => (init.method === "POST"
      ? new Response(JSON.stringify({ errors: [{ detail: "You already have a current Distribution certificate" }] }), { status: 409 })
      : capped.fetch(url, init)) });
  await assert.rejects(prepareSigning({ api, bundleId: BUNDLE_ID, distributionKey: MY_KEY, log: () => {} }),
    /already have[\s\S]*revoke one nobody uses/);
});

test("the distribution key must be one Apple will certify", async () => {
  const ec = generateKeyPairSync("ec", { namedCurve: "P-256" }).privateKey.export({ type: "pkcs8", format: "pem" });
  const small = generateKeyPairSync("rsa", { modulusLength: 1024 }).privateKey.export({ type: "pkcs8", format: "pem" });
  for (const key of [ec, small]) {
    await assert.rejects(run(world(), { distributionKey: key }), /2048-bit RSA/);
  }
});

test("secrets are read however they were pasted", () => {
  const want = `${MY_KEY.trim()}\n`;
  assert.equal(pem(MY_KEY, "K"), want);
  assert.equal(pem(MY_KEY.replace(/\n/g, "\r\n"), "K"), want);
  assert.equal(pem(Buffer.from(MY_KEY).toString("base64"), "K"), want);
  assert.equal(pem(MY_KEY.trim().replace(/\n/g, "\\n"), "K"), want);
  assert.throws(() => pem("hunter2", "IOS_DISTRIBUTION_KEY"), /IOS_DISTRIBUTION_KEY is not a PEM key/);
  assert.throws(() => pem("", "K"), /not a PEM key/);

  checkIds("2X9R4HXF34", "57246542-96fe-1a63-e053-0824d011072a");
  assert.throws(() => checkIds("57246542-96fe-1a63-e053-0824d011072a", "2X9R4HXF34"), /swapped/);
  assert.throws(() => checkIds("AuthKey_2X9R4HXF34", "57246542-96fe-1a63-e053-0824d011072a"), /10-character/);
  assert.throws(() => checkIds("2X9R4HXF34", "my team"), /UUID/);
});

test("ExportOptions asks for an App Store .ipa signed with the profile, on disk", () => {
  const xml = exportOptions({ teamId: TEAM, bundleId: BUNDLE_ID, profileName: PROFILE_NAME });
  const value = (key) => xml.match(new RegExp(`<key>${key}</key>\\s*<(\\w+)>?([^<]*)`))?.slice(1);
  assert.deepEqual(value("method"), ["string", "app-store-connect"]);
  assert.deepEqual(value("destination"), ["string", "export"]);
  assert.deepEqual(value("signingStyle"), ["string", "manual"]);
  assert.deepEqual(value("teamID"), ["string", TEAM]);
  assert.deepEqual(value(BUNDLE_ID.replace(/\./g, "\\.")), ["string", PROFILE_NAME]);
  assert.equal(xml.split("<dict>").length, xml.split("</dict>").length);
});

function withEnv(env, fn) {
  const saved = Object.fromEntries(Object.keys(env).map((k) => [k, process.env[k]]));
  Object.assign(process.env, env);
  const quiet = console.log;
  console.log = () => {};
  return fn().finally(() => {
    console.log = quiet;
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  });
}

const API_ENV = {
  APP_STORE_CONNECT_KEY_ID: "KEY1234567",
  APP_STORE_CONNECT_ISSUER_ID: "57246542-96fe-1a63-e053-0824d011072a",
  APP_STORE_CONNECT_KEY: Buffer.from(ASC_KEY).toString("base64"),
};

test("a run writes the files the build uses, the key readable only by it", async () => {
  const out = path.join(work, "out");
  const outputs = path.join(work, "github-output");
  writeFileSync(outputs, "");
  await withEnv({ ...API_ENV, IOS_DISTRIBUTION_KEY: MY_KEY, GITHUB_OUTPUT: outputs },
    () => main(["--out", out], { fetch: fakeApple(world()).fetch }));
  assert.deepEqual(readdirSync(out).sort(), [
    "ExportOptions.plist", "distribution.pem", "key.pem", "profile.mobileprovision",
  ], "and not the API key: the build runs the npm packages' code");
  assert.equal(statSync(path.join(out, "key.pem")).mode & 0o077, 0, "key.pem is private");
  const written = Object.fromEntries(readFileSync(outputs, "utf8").trim().split("\n")
    .map((l) => l.split(/=(.*)/s).slice(0, 2)));
  assert.equal(written.team_id, TEAM);
  assert.equal(written.app_id, "6700000001");
  assert.equal(written.bundle_id, BUNDLE_ID);
  assert.equal(written.profile_name, PROFILE_NAME);
  assert.match(written.profile_uuid, /^uuid-\d+$/);
});

test("--api-key-only writes the API key where altool looks, and nothing else", async () => {
  const out = path.join(work, "asc");
  let called = false;
  await withEnv(API_ENV, () => main(["--api-key-only", "--out", out],
    { fetch: async () => { called = true; } }));
  assert.equal(called, false, "no API calls");
  assert.deepEqual(readdirSync(out), ["private_keys"]);
  const p8 = path.join(out, "private_keys/AuthKey_KEY1234567.p8");
  assert.equal(readFileSync(p8, "utf8"), `${ASC_KEY.trim()}\n`, "decoded from the secret");
  assert.equal(statSync(p8).mode & 0o077, 0, "readable only by the runner's user");
});
