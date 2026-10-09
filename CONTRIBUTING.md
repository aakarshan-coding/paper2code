# Contributing

paper2code is a personal research project, but issues and pull requests are welcome.

- **Read first:** `docs/superpowers/specs/2026-09-30-paper2code-design.md` is the binding design and
  `docs/honesty.md` explains the defenses. A change that weakens a defense needs a canary that
  shows it still fires.
- **Test-first.** Write the failing test, watch it fail, make it pass, run the whole suite
  (`python -m pytest -q`, about three minutes, no network, no model).
- **No model, no network in the unit suite.** Anything that calls OpenAI, Modal or the Agent SDK
  is an opt-in live test behind an environment variable (`PAPER2CODE_LIVE_*`).
- **Secrets never land in the repository.** Keys live in the environment; the runs repository push
  token travels in a git header, never in a URL or a file.
- **Record decisions.** Anything non-obvious goes into `decisions.md` with the reason.
- **Style for replies and docs:** short sentences, plain words, technical terms explained on first use.

Open an issue before a large change so the design can be discussed first.
