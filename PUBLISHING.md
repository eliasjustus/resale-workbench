# Releasing Resale Workbench

Resale Workbench is released under the [MIT License](LICENSE) at [eliasjustus/resale-workbench](https://github.com/eliasjustus/resale-workbench). Version 0.1.0 is an ordinary GitHub release. Packages are distributed as GitHub release assets; no PyPI release is configured.

## Build and verify the release inputs

Keep operating data and historical research outside the public source tree. In a development environment with Python 3.11 or later:

```text
python -m pip install . build "setuptools>=77.0.3" wheel
python tools/check_candidate.py dist/checked-0.1.0
```

Use a fresh destination. `release-files.json` is the exact source allowlist, not a directory wildcard. Review additions to that file. The checker validates every path, rejects private directories, local settings, binary/media payloads and links/reparse points, scans common sensitive patterns, freezes checked bytes, and writes a hash inventory. Optional `--private-patterns PRIVATE_JSON` uses a private array of strings to reject owner-specific context without publishing those strings.

The checker uses the selected Python interpreter (override with `--python EXECUTABLE`), builds in the staged source without installing dependencies, and runs the full suite including archive/install checks. Build and test logs remain under `verification/`. Source hashes must remain unchanged; the exact `source.zip` and a passing `verification/report.json` are produced only after success. Run the check again after any source or metadata change.

The command makes no live source/model calls and does not publish. `tools/stage_release.py` is the lower-level staging-only command. Do not publish the whole candidate directory: verification logs can contain local paths, and build/cache files are not source-release inputs.

Pattern checks cannot establish that every secret or personal detail is absent. Review the candidate inventory and source contents. `.gitignore` cannot remove tracked files or clean Git history. Existing research archives are excluded from the release rather than sanitized in place.

## Publish the reviewed source and assets

For the first public repository, extract the checked `source.zip` into a new directory and initialize Git there. This starts public history from the allowlisted source instead of carrying private research history into GitHub. Confirm its files agree with `SOURCE-MANIFEST.json`, and keep verification logs and build output outside that new repository. For subsequent releases, work from the public repository and inspect the changes since the previous tag.

Before publishing, confirm the version is `0.1.0` in package metadata and runtime version output, the MIT License is included in the archives, and the release notes describe the actual behavior and validation limits. The wheel contains runtime packages and role resources; the source distribution and checked source ZIP include documentation and synthetic tests. Test installation outside the checkout, then run `resale doctor`, the synthetic demo and private workspace initialization. These checks do not establish live marketplace access or external model availability.

Push the reviewed source to [the GitHub repository](https://github.com/eliasjustus/resale-workbench), tag the matching commit `v0.1.0`, and create an ordinary GitHub release for that tag. Attach the verified wheel and source distribution from the checked candidate's `dist/`, plus its exact `source.zip`. Use the version's CHANGELOG entry for the release notes. Compare attached asset hashes with the local verified files. Keep local verification logs private. Do not use a prerelease flag for this release or describe it as production-validated.

CI is configured for Windows/Linux and Python 3.11/3.13. Check the actual remote workflow results and distinguish them from local test results in release notes. A configured matrix alone is not proof those combinations have passed. Independent onboarding and live valuation quality remain separate assessments.

## Preserve the data and source boundaries

The repository also contains a specifically owner-authorized selection of public
report derivatives under `private/research/2026-09-14/`, with an index and researcher
brief under `private/`. That directory name provides no privacy. These documents
are repository context and are deliberately absent from `release-files.json`,
the wheel, sdist and checked source ZIP. Do not broaden the release allowlist or
force-add the local private tree to include them. Review and stage individual
approved report paths; keep raw evidence, account data and local logs excluded.

Use invented fixtures for bug reproductions; do not put real marketplace evidence into tests. Shared role instructions live in `resale_tool/resources/REVIEWER.md` and `VALUATOR.md`; root pages link to them. Optional operator capabilities belong in private configuration.

The MIT License covers this software, not marketplace photos, listings or account data. Automated source access and evidence reuse/model processing require their own permission. See [Kleinanzeigen terms](https://themen.kleinanzeigen.de/nutzungsbedingungen/). Publishing or installing Resale Workbench does not start collection, dispatch agents or enable scheduling.

For accidental exposure, follow [SECURITY.md](SECURITY.md) and [GitHub's sensitive-data guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository). Genuine exposed credentials require revocation or rotation; deleting a file from the latest commit is insufficient. A fresh release archive cannot clean an existing repository's history.
