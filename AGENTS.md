# Project instructions

- Write repository content, source comments, commit messages, and GitHub text in
  English.
- The repository name is `exordos_opencode_server`; the Exordos element name is
  `opencode_server`.
- Do not vendor OpenCode source code or binaries. Download one pinned upstream
  release during the image build and verify its SHA-256 checksum.
- Keep OpenCode automatic updates disabled so the runtime remains identical to
  the reviewed build artifact.
- Keep Basic Auth enabled. The server password must originate from an Exordos
  Secret Manager resource and must never be committed or printed.
- Keep OpenCode state and the server workspace on the persistent element disk.
- Run the OpenCode service under the dedicated `opencode` system user and group.
- The server intentionally listens on the VM network interface. Document that
  the exported HTTP endpoint must be used only on a trusted network or behind a
  TLS-terminating authenticated proxy.
- Do not commit credentials, tokens, private URLs, infrastructure addresses,
  Workspace identifiers, user identifiers, or message contents.
- Verify manifest and service contracts against current Exordos Core and
  OpenCode documentation before changing them.
- Run `make check` before publishing changes. Build and deploy the element only
  through the `exordos` CLI.
