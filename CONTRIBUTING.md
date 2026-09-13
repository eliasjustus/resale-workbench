# Contributing to Resale Workbench

Report bugs and propose changes through [GitHub issues](https://github.com/eliasjustus/resale-workbench/issues), or open a pull request against the repository. Explain the problem and the resulting behavior. Use a separate private operating workspace, synthetic fixtures and bounded offline tests; never add personal configuration, real marketplace evidence or account state to source. Contributions are made under the project's [MIT License](LICENSE).

Run `python -m unittest discover -s tests -v`. Build and check distributions under PUBLISHING.md when packaging or file boundaries change. Update tests when behavior changes, and keep schema compatibility for retained historical runs. Code checks can verify references and declarations but must not be described as factual certification.

Edit role instructions only in `resale_tool/resources/REVIEWER.md` and `VALUATOR.md`; the root pages link there. New runs copy these canonical resources, while existing runs retain their frozen versions. Shared synthetic fixtures belong in test helpers, not another test class.

For a bug report, include your software/Python version, operating system, reproduction commands, expected result and actual result. Prefer a minimal synthetic example and, when useful, the reviewed output of `resale support-summary RUN --output support.json`. Remove personal paths or credentials from errors before posting. Do not attach a raw run, database, browser profile, conversation or screenshot containing private information.

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md), rather than opening a public issue with exploit details or sensitive evidence. The software license does not authorize collection or redistribution of third-party marketplace content.
