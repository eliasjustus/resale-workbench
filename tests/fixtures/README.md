# Frozen operations compatibility baseline

`operations-baseline.json` contains only invented inputs and observed deterministic
outputs from commit `aec6a133bd532eef3e61e402aa8698957cd00bc5`. It protects the
existing evaluator and old workspace reader while an optional operations layer is
developed. It is not transaction evidence, an operational policy or a trading result.

The fixture is independent of the current demo generator. Its case records retain
explicit synthetic notices but deliberately omit the demo's `synthetic` exemption
flag, so tests exercise normal gallery and sealed-comparable enforcement. Gallery
text bytes test integrity only; no actual photograph or visual inspection is claimed.

Expected results are complete semantic snapshots, including reason order,
comparison dispositions, decimal strings, historical replay labels and lack of
purchase authority. The old retained-evidence bindings also freeze schema and file
hashes, so simultaneous changes to the current writer and reader cannot hide an
incompatible old review. Only `${CAPTURE}` and `${ROOT}` path prefixes and platform
separators are substituted when materializing a temporary old workspace. Source
record hashes bind the temporary serialization. Frozen retained/playbook bytes
retain their original line endings; the old SQLite table definitions are restored
without calling the current queue initializer. Test outputs never overwrite inputs.

Do not regenerate these expectations from a changed evaluator or fixture builder.
An intentional change to an existing public contract needs its own compatibility
decision and explicit versioned fixture; preserve this baseline. Future operational
records use a separate namespace and must not enter historical economic replay.

Run `python -m unittest discover -s tests -p test_operations_compatibility.py -v`.
The distribution suite also runs this same file against the installed wheel outside
the checkout, after checking every declared runtime module and resource.

`operations-draft.schema.json` preserves the original eight-type design before
explicit adoption. `operations-records.json` contains invented positive examples
for the adopted operational types. These are separate from the historical
compatibility baseline and contain no retained marketplace or operator evidence.
