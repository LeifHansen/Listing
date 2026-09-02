# Universal / App Links

These files are served at the domain root and are what iOS and Android read to
decide that a link to this site may open in the app instead of the browser.

They are deliberately **empty but valid** right now. iOS is in TestFlight and
Android is unreleased, so there is no Team ID or signing certificate to publish
yet — and a file containing a wrong ID is worse than one containing none, since
the OS caches what it fetches.

## Filling them in

`apple-app-site-association` — no file extension, served as
`application/json`. Add the App ID (`<TeamID>.com.thryftshop.app`) and the
paths that should open in the app:

```json
{
  "applinks": {
    "details": [
      { "appID": "ABCDE12345.com.thryftshop.app", "paths": ["/listing/*", "/app/*"] }
    ]
  }
}
```

`assetlinks.json` — Android. Needs the SHA-256 fingerprint of the signing
certificate that the Play release is signed with (Play Console → App integrity
→ App signing), not the local debug key:

```json
[{
  "relation": ["delegate_permission/common.handle_all_urls"],
  "target": {
    "namespace": "android_app",
    "package_name": "com.thryftshop.app",
    "sha256_cert_fingerprints": ["AA:BB:..."]
  }
}]
```

Verify after deploying with Apple's CDN
(`https://app-site-association.cdn-apple.com/a/v1/<domain>`) and Google's
Digital Asset Links API.
