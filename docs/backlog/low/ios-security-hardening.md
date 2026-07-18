# iOS Hardening: ATS Arbitrary Loads, Team ID & Tailscale IP in History

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** low
**Severity (audit):** low
**Source:** Public-release readiness audit 2026-07-18 (findings #44, #45, #47)

## Summary

Three low-severity hygiene items in the `alfred-ios` client to settle before the first
public push (the repo is still private, so all three are cheap to address now). None leak
credentials or credential values. (1) `Info.plist` globally disables App Transport Security,
allowing plaintext HTTP to any host. (2) The Apple Developer Team ID is tracked in the
Xcode project files and history. (3) A historical Tailscale IP remains recoverable from git
history. Each is either a deliberate design choice or a low-sensitivity account/tailnet
linkage rather than a secret leak, but they affect public forks, App Store viability, and
trace minimization.

## Context / Motivation

**ATS arbitrary loads (#47).** The tracked `alfred-ios/App/Alfred/Resources/Info.plist`
sets `NSAppTransportSecurity → NSAllowsArbitraryLoads = true`, permitting plaintext HTTP to
any host. This is a deliberate design choice — the app talks HTTP to a self-hosted server
over Tailscale, which the README correctly names as the trust boundary — not a leak. Once
public, however, users who point the app at a non-Tailscale address get zero transport
security, and a blanket ATS exemption will also block any future App Store submission. There
are uncommitted `Info.plist` edits in the working tree.

**Team ID in project files + history (#44).** `DEVELOPMENT_TEAM = A3PH6PGY29` appears in the
tracked `alfred-ios/Alfred.xcodeproj/project.pbxproj` (app target build configs) and in the
stale XcodeGen `alfred-ios/project.yml` (still tracked even though commit `a8d7093` "drop
XcodeGen" abandoned it), and remains in history at `8ebce6a:Alfred.xcodeproj/project.pbxproj`.
Team IDs are embedded in every distributed app binary and cannot be used to sign without the
owner's certificates, so this is low sensitivity — but it ties the repo to the owner's paid
Apple Developer account, and public forks will fail signing until contributors swap in their
own team. No `.p8` keys or provisioning profiles are involved.

**Tailscale IP in history (#45).** Commit `c8409d6` "Remove hardcoded Tailscale IP from
release ServerConfig default" scrubbed `100.100.1.1` from the working tree (current defaults:
`localhost` in DEBUG, empty host in RELEASE — verified clean), but the IP is still
recoverable from history in `ServerConfig.swift`, `CLAUDE.md`, and DI/tests
(`c8409d6~1:App/Alfred/Domain/Entities/ServerConfig.swift`,
`c8409d6~1:CLAUDE.md`,
`c8409d6~1:Tests/AlfredTests/Domain/EntityTests.swift`). Exposure is minimal: `100.100.1.1`
is inside the Tailscale CGNAT range (`100.64.0.0/10`), non-routable from the public internet,
and only meaningful to devices already inside the owner's tailnet.

## Acceptance Criteria

- [ ] ATS: either retain `NSAllowsArbitraryLoads` with the existing README caveat, or narrow
  the `Info.plist` exemption to `NSAllowsLocalNetworking` plus a documented exemption.
- [ ] README notes the App Store submission implication of the ATS exemption.
- [ ] Pending working-tree `Info.plist` edits are reconciled/committed (no uncommitted ATS
  changes left dangling).
- [ ] Team ID: either blank `DEVELOPMENT_TEAM` in `alfred-ios/Alfred.xcodeproj/project.pbxproj`
  (contributors set their own via Signing & Capabilities), or explicitly record the decision
  to ship as-is accepting the paid-account linkage.
- [ ] Stale `alfred-ios/project.yml` (abandoned by commit `a8d7093` "drop XcodeGen") is deleted.
- [ ] Tailscale IP: record a decision to either publish as-is (justified by CGNAT
  non-routability) or, for zero trace, rotate the device's Tailscale IP / rewrite history with
  `git filter-repo` before the first public push.
