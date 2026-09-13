# Resale Workbench

A local tool for collecting and reviewing resale evidence. It keeps observations, transaction evidence, estimates, unknowns and human decisions separate. The included economic checks support Germany/EUR; the optional marketplace collector supports Kleinanzeigen.

The software can run an offline demonstration and a manual review without an account, model subscription or browser. Agent preparation and valuation are manually coordinated. Live access depends on source permission and your environment. Passing checks does not establish that a sale happened, a repair is feasible or an opportunity will be profitable.

Resale Workbench is released under the [MIT License](LICENSE). Source and issues are on [GitHub](https://github.com/eliasjustus/resale-workbench); version 0.1.0 is distributed through [GitHub Releases](https://github.com/eliasjustus/resale-workbench/releases). The command remains `resale` to keep existing workspaces and scripts compatible. The software license does not grant rights to third-party listings, photos or account data.

## Install and try it

Python 3.11 or later. Clone the source and install it in a virtual environment:

```powershell
git clone https://github.com/eliasjustus/resale-workbench.git
cd resale-workbench
python -m venv .venv
.\.venv\Scripts\python -m pip install .
.\.venv\Scripts\resale doctor
.\.venv\Scripts\resale demo --output demo.local
```

On Linux/macOS use `.venv/bin/python` and `.venv/bin/resale` instead. Installation downloads ordinary package dependencies; the doctor and demo commands do not use the network. Open `demo.local/review.html` to inspect three fictional outcomes and their inputs. Every demonstration result is labelled synthetic.

Alternatively, download the `.whl` asset from [GitHub Releases](https://github.com/eliasjustus/resale-workbench/releases), create a virtual environment, and run its Python with `-m pip install PATH_TO_WHEEL`, replacing `PATH_TO_WHEEL` with the downloaded file. Then run `resale doctor` and `resale demo` from that environment. There is no PyPI release; a bare package-name install is not the documented installation route. Source installation also includes the contributor guides and tests.

For the following commands activate the environment (`.\.venv\Scripts\Activate.ps1` on PowerShell, or `source .venv/bin/activate` on bash). If activation is unavailable, use the full executable path above.

```text
resale init ../my-review-workspace
resale doctor --config ../my-review-workspace/resale.toml
```

Edit that local TOML file: choose your search location/radius, categories, model identifiers for any external executor, economic screening policy and optional capabilities. Each initialized workspace uses its own private-data directory. The example location and model names are editable examples. No model availability or source access is implied by valid configuration. The generated workspace is ignored in Git by default.

New runs freeze configuration and instructions; editing configuration affects future runs. Existing run manifests retain their original behavior. Preparation has a configurable time window; listing/job count caps are optional. These limits do not stop already running external agents or meter model tokens/cost.

Use `resale run`, `resale search` and `resale capture` for coordinated work. `resale run init --config WORKSPACE/resale.toml` creates a run beneath that workspace and prints its full `run_path`. A sole configured category is inferred; with multiple categories supply `--category`. An explicit run path remains available. `resale run status RUN` reports the current stage, integrity issues and next action without preparing jobs, dispatching workers or writing reports.

## Review a retained case

A collector capture is a private folder containing `record.json`, source text and photos. Review the original locally. Use only evidence you are entitled to retain and process.

```text
resale draft-review CAPTURE --config WORKSPACE/resale.toml --output review-draft.json
```

Complete the new JSON draft with inspected identity/condition, actual audited transaction references and established costs. Unknown fields start unresolved; do not fill them to make a check pass. Comparable `source_paths` refer to files under an evidence directory you choose. The synthetic demo's review JSON shows the field structure; its invented evidence must never be reused for a real item. RUNNER.md describes the evidence contract.

After inspecting and completing the draft, bind its retained comparable files and evaluate into a new result:

```text
resale seal-review CAPTURE review-draft.json --evidence-root EVIDENCE --output review-sealed.json
resale evaluate CAPTURE review-sealed.json --output result.json
```

The result retains the selected economic policy. `--run RUN` can replace `--config` when creating a draft from an integrity-checked run's frozen policy. Later `--config`/`--run` evaluation overrides must agree with that policy. Sealing records file hashes; it does not independently audit transactions or approve the operator's judgments. Supported manual results require a retained original gallery and bound comparable evidence; missing or changed files are rejected.

The compatibility command `python -m evaluation check REVIEW --output RESULT` now uses the same current evaluation service and retained-file checks. Relative `source_capture` paths resolve beside the review for current reviews. Schema-1 historical reviews require explicit `--legacy-replay` (also available on `resale evaluate`); those outputs are marked as historical replay, and are not current retained-evidence verification. Preserve original reviews and create a new schema-2 draft for a current assessment.

A `supported` result meets the evidence and arithmetic policy, `unsupported` records an evidenced exclusion or insufficient margin, and `unresolved` means the necessary evidence or costs are missing. No result authorizes a purchase.

## Prepare model inputs privately

Price masking is separate from privacy review. Original captures stay private. Before supplying a target to an external model, create an editable copy:

```text
resale privacy-workspace CAPTURE EDITABLE
```

Edit `EDITABLE/source-text.json` and redact the copies of images when needed using your own editor. Inspect contact details, device/network credentials, faces, labels, addresses, QR codes and image metadata; inspect every frame of animated files. Automated text flags are advisory and never certify anonymity. Keep limitations explicit when redaction removes useful evidence.

After editing, create and complete the review template, then export:

```text
resale privacy-template CAPTURE EDITABLE --output privacy-review.json
resale prepare-private CAPTURE MODEL_INPUT --sanitized EDITABLE --review privacy-review.json
resale check-privacy CAPTURE MODEL_INPUT
```

The template deliberately starts pending. Record the actual reviewer/time, text assessment, per-photo visual/metadata assessment, transformations and evidence limitations. Any subsequent text/image change requires a fresh review. The output contains reviewed text/images and model-visible redaction limitations. A separate sibling review file holds private audit notes and is linked by hash. Give a worker only its assigned packet and job instructions; keep the original capture and audit sibling private. Source-independent research also needs a privacy review before transfer to another worker.

Price-leak inspection remains required. Neither reviewed model input nor source capture is automatically safe to publish. This manual executor uses procedural isolation; it does not create a filesystem sandbox or configure provider data handling. PRIVACY.md describes these boundaries.

## Optional collection and agent jobs

Install the browser extra from this local source directory only when an authorized collection workflow needs it:

```text
python -m pip install ".[browser]"
python -m playwright install chromium
resale capture --help
resale search --help
resale run --help
```

The collector uses a separate logged-out browser, retains original evidence and stops on HTTP access blocks. It is not an official marketplace integration. Kleinanzeigen's terms require express written consent for automated collection and restrict content reuse; technical access and low request frequency do not establish permission. See [Kleinanzeigen terms, section 5](https://themen.kleinanzeigen.de/nutzungsbedingungen/). This prerequisite remains separate from software installation. No live collection is part of the offline demo or test suite.

For manually coordinated work, initialize a run with one category from your configuration:

```text
resale run init WORKSPACE/runs/first --config WORKSPACE/resale.toml --category computers
```

In your browser, choose the matching native city, radius, category and newest-first ordering. Copy the resulting clean search URL. Category labels in TOML are your own names: map them to a suitable native category yourself and describe that mapping in the notes. For the template's Berlin/50 km/computers settings:

```text
resale search prepare WORKSPACE/runs/first --url "COPIED_NATIVE_SEARCH_URL" --center Berlin --radius-km 50 --category computers --notes "Describe the native category you inspected and the newest-first ordering"
resale search collect WORKSPACE/runs/first
resale run import-discovery WORKSPACE/runs/first
resale run status WORKSPACE/runs/first
```

Replace the URL and notes with actual observed values. Preparation is offline and freezes a `search-plan.json` against authoritative run state. Collection uses the bound URL and checks native city/radius and category/location IDs, including redirects and pagination. The current URL adapter accepts native city/category searches ending `c<CATEGORY_ID>l<LOCATION_ID>r<KM>`; unsupported URL forms fail with guidance. Use the native city name in `search.center`. Changing scope requires a new configured run and prepared search.

Import defaults to `RUN/discovery/rows.json` and `summary.json`. Use `--discovery-dir DIRECTORY` for another collector output folder, or both `--rows` and `--summary` for explicit external inputs. Incomplete coverage remains incomplete after import. `resale capture URL --workspace WORKSPACE` stores retained captures beneath `WORKSPACE/captures`; `--output` explicitly overrides that location. Capture and search collection use the network only when invoked. Existing `python -m pilot`, `python -m collector` and `python -m collector.discover` entry points remain compatibility commands.

This is supervised source setup, not automatic geocoding or semantic category matching. Inspect each selected listing's actual location/category; promoted or miscategorized listings can still appear. Follow RUNNER.md for triage, private evidence and jobs. Jobs contain frozen research criteria, role/model specifications and explicit file assignments. A coordinator must execute and reconcile them; no API charges or automatic model calls are hidden in the Python commands. The optional browser-capture JavaScript helper targets the documented browser-tool interface, not arbitrary Playwright objects.

## Development and release checks

```text
python -m unittest discover -s tests -v
python tools/check_candidate.py dist/checked
```

The second command stages the exact `release-files.json` allowlist, builds its wheel/source distribution, runs the full suite against that candidate and creates an exact source ZIP only after success. It requires the local development tools `build`, `setuptools>=77.0.3` and `wheel` (`python -m pip install build "setuptools>=77.0.3" wheel`); the check itself does not install packages or contact sources. It refuses an existing destination and never publishes. Use a new destination for each candidate. Optional `--private-patterns PRIVATE_JSON` rejects owner-specific strings without including them in the candidate. `tools/stage_release.py` remains available for staging alone.

`data/`, `runs/`, research history, old experiment fixtures, local settings, databases and private notes are not release inputs. Keep operating data in a separate private workspace. The built wheel contains explicit runtime packages/resources; the source release includes documentation and synthetic tests. Build and verification details are in [PUBLISHING.md](PUBLISHING.md).

For assistance, `resale support-summary RUN --output support.json` exports only fixed fields and aggregate counts, without original text/images, listing IDs, paths, task IDs or timestamps. Review it before sharing. Do not upload a whole workspace or raw run as a bug report.

Use [GitHub issues](https://github.com/eliasjustus/resale-workbench/issues) for bugs with synthetic reproductions; see [CONTRIBUTING.md](CONTRIBUTING.md). Report security vulnerabilities privately through [SECURITY.md](SECURITY.md).
