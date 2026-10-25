# Preserve the installed app's consent identity during updates

The installer previously checked bundle/executable and runtime ownership but did
not compare code-signing identity with the existing Alden installation. A new
ad-hoc or differently signed build could therefore replace the installed app and
cause another macOS consent request. This is an update-path defect; the latest
observed permission dialog has not been causally attributed to that path.

Updates now read each app's designated requirement, verify the installed signature,
verify the candidate against the installed requirement, and require the candidate's
designated requirement to remain identical. The checks run on the source bundle
before backup/staging or launchd activity, then again on the staged copy before
stopping a runtime. Missing, invalid, multiple or changed requirements stop the
update. Application content/CDHash can change while the consent identity stays
the same. A first installation has no existing identity to compare and is reported
separately; an intentional signer migration needs its own reviewed procedure.

The following supported check performs static reads without installing or starting
an app, staging files, changing permissions or contacting launchd:

```sh
sh scripts/install-alden-desktop.sh --check-identity-only
```

Normal installer stdout remains unchanged. Test-only code-signing adapters require
the existing explicit installer test gate and do not change the production tool.
The build's existing local certificate-fingerprint profile remains unchanged;
CI's unsigned-build behavior is also unchanged.

Validation covers stable identities with different app content, changed signer,
ad-hoc identity, missing/ambiguous requirements, invalid signatures, ordinary
preflight rejection and staged-copy identity changes before runtime stop. Existing
launcher, rollback, duplicate-process, Python admission and build-receipt tests
are retained. The new boundary test failed in all seven original cases before
implementation; the complete 76-test affected suite now passes.

On the actual Mac, the installed 0.3.219 and frozen 0.3.220 candidate have different
CDHashes but identical designated requirements and valid strict/deep signatures.
The static continuity check passes, and the configured fingerprint matches the
installed certificate. System Settings displays Alden Full Disk Access as on;
no toggle, TCC reset, database write, signer change or application restart was
performed. A direct read of the protected TCC database was unavailable and was
not retried through elevated access.

These checks prevent this installer from silently replacing the consent identity.
They do not prove the effective TCC record or guarantee that macOS can never
request consent again. The user keeps Alden off, so a subsequent actual launch
without another prompt remains unverified. Current-source external review and
the wider native/install/release gates remain separate and incomplete.
