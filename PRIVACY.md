# Private data and reviewed inputs

The code and synthetic tests can be separated from private operation. The development workspace itself may still contain personal history, original marketplace captures and previously identified sensitive imagery. Never distribute it wholesale. Use an allowlisted source candidate under PUBLISHING.md.

| Representation | Purpose | Sharing boundary |
| --- | --- | --- |
| Original capture, state database, raw research and browser output | Private source evidence and audit history | Keep private, including backups and database side files. |
| Editable privacy workspace | Local copies for manual sanitization | Unreviewed until every declaration is completed against the exact bytes. |
| Reviewed model-input derivative | Minimal text/images with explicit redaction limitations | Send only to a deliberately selected worker/provider after separate price checks. It is not certified anonymous or licensed for public distribution. |
| Sibling privacy review | Actor/time, review notes, original/derived hashes | Private; do not include with model input. Later queue operations recheck its binding. |
| Minimal support summary | Fixed schema and aggregate stage/outcome counts | Review before sharing. It excludes raw evidence and freeform fields. |
| Source candidate | Allowlisted code, documentation and synthetic tests | Publication still needs a separate decision and licensing/access review. |

`resale privacy-workspace` preserves originals and seeds copies. Manually inspect text, images, every animation frame, EXIF/other metadata and embedded codes; remove or obscure private details in the copies using an appropriate editor. The tool does not perform image redaction, OCR or metadata removal itself. Text detectors are advisory, report categories rather than matched private values, and miss names, addresses and many other identifiers. A clean detector result is not a privacy assessment.

Create a privacy template only after editing. Mark it reviewed only after the recorded text, all images and metadata have actually been inspected. Record meaningful limitations when redaction hides product identity, condition or other relevant evidence. If no material limitation is identified, say that explicitly. Copying credentials into notes or limitations would create a new disclosure; describe the category, never the value.

Exports verify source-record and original-image hashes, derived-image/text hashes, complete gallery coverage and explicit review declarations. They reject extra files, escaping paths and links/reparse points. They cannot verify that a human really inspected the evidence or that the chosen edits remove every private detail. Image redaction can alter evidential value; the derivative records that limitation instead of pretending it is an unchanged original.

Price blinding has separate requirements: numbers without currency, words, images and prior worker context can leak the target price. Original captures, provenance siblings, run reports and local configuration may contain prices or personal context. Supply only assigned worker inputs. The manual executor does not enforce filesystem isolation; fresh context alone is not a sandbox.

Original-source research packets can also contain account navigation, seller contacts and session fields. Inspect them before worker-to-worker transfer. No automatic whole-page anonymizer is provided. The chosen external model/browser tools determine where transmitted data is processed; local collection does not mean all processing remains on the user's machine.

For support use `resale support-summary RUN --output NEW.json`. This command opens SQLite read-only and emits fixed fields and counts only. It never sends anything. Avoid raw error logs, screenshots, source captures and whole-run ZIP files in public issues. Source staging also avoids exporting local configuration, personal patterns, user-directory paths and task links.

Retention is an operator decision. Preserve originals while they are needed for audit; keep sensitive captures under appropriate private filesystem access and backup controls. Removing evidence invalidates later integrity verification. If evidence must be removed, first stop dependent work, inventory all originals/derivatives/audit copies/backups, and record the deliberate removal privately. The software does not provide an automatic purge command or promise complete deletion across backups/external providers. Do not edit old manifests to claim deleted evidence remains available.
