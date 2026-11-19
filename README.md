# MCP Guardian

MCP Guardian is a security gateway between an MCP host (such as Claude Desktop) and tool servers. Each tool call shows an approval popup. The tool runs only after the user clicks **Allow**. Block, timeout, popup failure, or any Guardian error denies the call.

## Scope

| Feature | Status |
|---|---|
| Claude Desktop connection (STDIO) | Implemented |
| Approval popup: Allow, Block, timeout, one-at-a-time queue | Implemented |
| Fail-closed handling: unknown tool, invalid arguments, popup failure, upstream error | Implemented, unit-tested |
| Built-in Gmail tools: `search_mails`, `create_draft` | Implemented |
| Additional STDIO upstream servers | Implemented |
| Claude Web (remote HTTPS gateway) | Not implemented |

## How it works

```
Claude Desktop --STDIO--> Guardian --> Gmail API (built-in)
                             |     --> other STDIO MCP servers (optional)
                             +-- approval popup (Allow / Block)
```

- `guardian/guard.py`: the only path to tools. Validates the tool name and arguments, asks for approval, and forwards only after Allow.
- `guardian/approval.py`: tracks each request by ID. Timeouts, duplicate or stale decisions, and shutdown all deny.
- `guardian/ui.py`: the approval popup.
- `guardian/gmail.py`: Gmail tools and OAuth token handling.
- `guardian/gmail_auth.py`: one-time Gmail authorization command.
- `guardian/upstream.py`: connects to optional STDIO servers. Tools are named `<server>__<tool>`.
- `guardian/audit.py`: writes to `logs/guardian.log` and stderr, with sensitive values redacted. Stdout is reserved for the MCP protocol.

## Requirements

- Python 3.12 or newer
- A Google Cloud OAuth client of type **Desktop app**, and a test Gmail account
- Claude Desktop
- A desktop session that can display a window

On Linux, the popup needs the Qt runtime libraries, such as `libgl1`, `libxkbcommon0`, and `libdbus-1-3`, installed through your package manager.

## Setup

### 1. Create the environment

From the repo root:

```
python3.14 -m venv .venv                  # any Python 3.12+
<venv activate command>                   # see below
pip install -r requirements.txt
```

| System | Activate | Venv Python |
|---|---|---|
| macOS / Linux | `source .venv/bin/activate` | `.venv/bin/python` |
| Windows | `.venv\Scripts\activate` | `.venv\Scripts\python.exe` |

### 2. Run the tests

```
<venv python> -m pytest -v
```

### 3. Set up Google OAuth

1. In Google Cloud Console, enable the Gmail API and configure the OAuth consent screen.
2. Under **Audience → Test users**, add your test Gmail address.
3. Create an OAuth client of type **Desktop app**.
4. Create a `.env` file in the repo root, based on `.env.example`:

   ```
   GMAIL_CLIENT_ID=<your client ID>
   GMAIL_CLIENT_SECRET=<your client secret>
   ```

   Save the file as plain text with no quotes. Do not share these values or commit the file.

5. Authorize Gmail once, from the repo root, with the venv active:

   ```
   python -m guardian.gmail_auth
   ```

   Open the URL it prints, sign in with the test account, and approve access. The token is saved to `/token.json` with restricted permissions.

Testing-mode tokens expire after about seven days. When that happens, run the same command again.

### 4. Configure Guardian

```
cp guardian.config.example.json guardian.config.json
```

- `gmail.enabled` turns the built-in Gmail tools on or off.
- `gmail.allowed_tools` limits which tools are exposed. The default is `search_mails` and `create_draft`.
- `upstreams` lists optional STDIO MCP servers. Leave it empty to use only the built-in tools.

`guardian.config.json` and `.env` are gitignored.

### 5. Connect Claude Desktop

Open Claude Desktop, go to **Settings → Developer → Edit Config**, and add the `gmail-guardian` entry under `mcpServers` at the top level of the file. Replace the placeholders with your paths. On Windows, escape backslashes as `\\`.

```json
{
  "mcpServers": {
    "gmail-guardian": {
      "command": "<REPO_PATH>/.venv/bin/python",
      "args": ["<REPO_PATH>/guardian/main.py"]
    }
  }
}
```

On Windows, use `<REPO_PATH>\\.venv\\Scripts\\python.exe` for `command` and `<REPO_PATH>\\guardian\\main.py` for the argument.

Fully quit Claude Desktop, including from the system tray, then reopen it. Remove any direct Gmail connector in Claude, or Claude can reach Gmail without the popup.

## Running

Claude Desktop starts Guardian automatically. To run it manually for debugging, activate the venv and run:

```
python guardian/main.py
```

It reads MCP messages from stdin, so start it from a terminal and stop it with Ctrl+C. A healthy start logs `serving N exposed tools over STDIO`.

## Logs

- `logs/guardian.log` records each decision: `approval requested`, then `ALLOWED` or `BLOCKED`, then `OUTCOME`.
- Search queries appear in plain text. Keep the log private.
- Tokens are never logged. Fields such as `body` and `content` are redacted.

## Test checklist

- [ ] `pytest` passes
- [ ] `gmail_auth` saves a token, and Guardian starts with the expected tool count
- [ ] **Allow:** a Gmail search shows the popup, and Allow returns results
- [ ] **Block:** clicking Block denies the call, and the log shows `BLOCKED` with no forwarded `OUTCOME`
- [ ] **Draft block:** blocking a draft creates nothing in Gmail
- [ ] **Draft allow:** allowing a draft creates one draft, and nothing is sent
- [ ] **Timeout:** an unanswered popup is denied after 90 seconds
- [ ] **Concurrency:** several calls show one popup at a time, each with its own decision
- [ ] **Popup closed:** closing the popup while requests are pending denies them

## Security notes

- Guardian fails closed. Timeouts, popup failures, invalid decisions, and upstream errors deny the call or return an error. Tools are never called without Allow.
- The Gmail tools request read and compose scopes only. Sending is not implemented.
- Use a test Gmail account, not a primary inbox.
- Never commit `.env`, `guardian.config.json`, OAuth JSON files, token files, or `logs/`.

## Limitations

- Only Gmail is built in. Other services need a STDIO MCP server.
- Testing-mode Google tokens expire after about seven days.
- Claude Web (remote mode), pairing, and per-user isolation are not implemented.
- The Gmail code has not yet been run against a live account.
