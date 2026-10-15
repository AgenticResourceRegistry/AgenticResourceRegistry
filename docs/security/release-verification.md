<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Verify an Observal release

Observal release artifacts have GitHub keyless Sigstore provenance attestations. Release tags created after this policy was introduced are signed separately with gitsign. These checks prove different things:

* Artifact verification binds a downloaded file and its SHA-256 digest to the Observal release workflow.
* Tag verification binds a Git tag to the same GitHub Actions workflow identity.

Historical tags may be unsigned. Never treat an artifact checksum alone as proof of origin.

## Download the release

For a release produced by the release-branch pipeline, replace `v1.14.0` and `release/1.14` below with the exact version and minor line you want to verify:

```bash
gh release download v1.14.0 --repo Observal/Observal --dir observal-release
cd observal-release
```

## Check the downloaded bytes

The release includes `checksums.txt`:

```bash
sha256sum --check checksums.txt
```

On macOS, use `shasum -a 256 --check checksums.txt`.

## Verify artifact provenance

Install the [GitHub CLI](https://cli.github.com/) and verify each artifact you intend to run:

```bash
gh attestation verify ./observal-linux-x64 \
  --repo Observal/Observal \
  --bundle ./build-provenance.intoto.jsonl \
  --signer-workflow Observal/Observal/.github/workflows/release.yml@refs/heads/release/1.14
```

Use the downloaded server archive or another CLI binary in place of `observal-linux-x64`. A successful result verifies the artifact digest, Sigstore certificate chain, source repository, and release workflow identity.

GitHub keyless signing uses short-lived credentials issued to the workflow. Observal has no long-lived release private key stored on GitHub Releases or another download site.

## Verify a release tag

Install [gitsign](https://github.com/sigstore/gitsign), clone the repository, and fetch the tag:

```bash
git clone https://github.com/Observal/Observal.git
cd Observal
git fetch origin tag v1.14.0
gitsign verify-tag v1.14.0 \
  --certificate-identity https://github.com/Observal/Observal/.github/workflows/release.yml@refs/heads/release/1.14 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The expected Fulcio certificate identity is the release workflow on the exact matching `refs/heads/release/X.Y` branch. The expected OIDC issuer is GitHub Actions. Verification also checks the signature's transparency-log evidence.

A signed tag does not replace artifact provenance verification. Verify both when establishing the source commit and the exact downloaded bytes matters.

Historical releases made before the release-branch migration used `refs/heads/main` as their signing identity. Verify those historical artifacts against that original identity explicitly. The new publishing workflow never falls back to the historical identity or accepts an unsigned tag.
