# Security Policy

## Supported versions

pyKiro has no release yet; fixes land on `main`. Once releases exist, only the latest one receives fixes.

## Reporting a vulnerability

Please **don't** open a public issue. Report it privately through this repository's GitHub Security Advisories
("Report a vulnerability" on the Security tab), with a description, steps to reproduce and the impact you see.

## How pyKiro handles security

- **Your login stays in kiro-cli.** The library never reads Kiro's credential, session or log files, and never
  writes into your `~/.kiro`.
- **Isolated by default.** Sessions run with a private, temporary Kiro home, so none of your own agents, MCP servers
  or rules load unless you ask for `Inherit()`. The workspace's own `cwd/.kiro` configuration still loads; see
  `forbid_workspace_config` in [Project Config and Profiles](docs/11_Project_Config_and_Profiles.md).
- **Every permission ask that isn't for the session's own app tools is rejected unless your policy returns
  `True`.** Kiro's own rules decide first, and `deny` rules can't be overridden by a policy. Asks for the session's
  app tools are approved by the library, and only when Kiro's consent context and its host-resolved MCP identity
  both name the session's `app` server and one of its tools. While a session has app tools, the MCP server name
  `app` is reserved: a configured server of that name is refused. Cancelling a turn answers its pending asks
  `cancelled` at once, and an answer your policy gives after that is not sent.
- **App tools are served on 127.0.0.1 only**, each session at its own random, unguessable path. That path is the
  only credential. A request with a foreign `Origin` header is refused. A tool's unexpected exception is logged
  locally; the agent only learns that the tool failed (raise `ToolError` for a message it should see).
- **Debug wire logs leave MCP server definitions out.** With the `pykiro.wire` logger at DEBUG, each MCP server
  definition is logged as its name and type only (no URL, headers, command arguments or environment). The rest of
  the wire traffic, including prompts, replies and account details, is logged as it is, so never post a wire log in
  a public issue.
- **No shell-based process launch.** kiro-cli is started with an argument list in its own process group, never
  through a shell, and there is no `eval`, `exec` or `pickle` anywhere.
- **Terminal output is filtered.** `AnsiRenderer` drops terminal control characters from model- and tool-written
  text, so a reply can't drive your terminal (clipboard, title, cursor).
- **Checked on every change:** Bandit scans the source in `just check` and CI.
