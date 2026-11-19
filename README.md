# MCP Guardian

**A security and approval gateway for Model Context Protocol (MCP) tool execution.**

MCP Guardian is a security middleware layer between an MCP host (such as Claude Desktop) and MCP tool servers. It intercepts tool calls and requires explicit user approval before allowing them to execute.

Every tool invocation triggers an approval popup where the user can choose **Allow** or **Block**. A tool executes only after explicit approval. If a request times out, the popup fails, an invalid decision is received, or Guardian encounters an internal error, the request is denied.

The goal is simple: **No tool execution without user approval.**

## Architecture

```text
                  MCP Host
               Claude Desktop
                      |
                      | MCP (STDIO)
                      v
              +------------------+
              |   MCP Guardian   |
              |                  |
              |  Tool Discovery  |
              |  Validation      |
              |  Approval        |
              |  Audit Logging   |
              +------------------+
                      |
             +--------+--------+
             |                 |
             v                 v
       Built-in Tools    Upstream MCP Servers(extendeble)
             |                 
             v                 
          Gmail API       

                      |
                      v
                Approval Popup
                 Allow / Block
```

## Execution Flow

Every tool call follows the same controlled execution path:

1. **Tool Discovery:** Guardian discovers tools from its built-in integrations and configured upstream MCP servers.
2. **Tool Invocation:** The MCP host sends a tool call to Guardian.
3. **Validation:** Guardian validates the requested tool name and arguments.
4. **Approval Request:** Guardian creates an approval request and displays the popup.
5. **User Decision:** The user explicitly chooses Allow or Block.
6. **Execution:** If approved, Guardian forwards the request to the corresponding tool implementation or upstream server.
7. **Response:** Guardian returns the execution result or error to the MCP host.
8. **Audit Logging:** The approval decision and execution outcome are recorded.

### Fail-Closed Security

Guardian follows a fail-closed execution model:

- Only an explicit Allow decision permits execution.
- Block decisions immediately deny execution.
- Approval timeouts result in denial.
- Popup failures and unexpected UI errors result in denial.
- Unknown tools and invalid arguments are rejected.
- Duplicate or stale approval decisions are rejected.
- Shutdown with pending requests denies those requests.
- Upstream execution errors are returned without bypassing the approval gateway.

## Project Structure

```text
MCP-Guardian/
│
├── guardian/
│   ├── __init__.py
│   ├── main.py
│   ├── guard.py
│   ├── approval.py
│   ├── ui.py
│   ├── gmail.py
│   ├── gmail_auth.py
│   ├── upstream.py
│   └── audit.py
│
├── tests/
│
├── logs/
│
├── .venv/
├── .env
├── .env.example
├── token.json
├── guardian.config.json
├── guardian.config.example.json
├── requirements.txt
├── pytest.ini
└── README.md
```

### Core Components

| Component | Responsibility |
|---|---|
| `main.py` | Application entry point and MCP server initialization |
| `guard.py` | Central tool execution gateway, validation, approval enforcement, and forwarding |
| `approval.py` | Approval request lifecycle, request IDs, timeouts, and decision handling |
| `ui.py` | User approval popup |
| `gmail.py` | Built-in Gmail tool implementations and OAuth token handling |
| `gmail_auth.py` | Gmail OAuth authorization |
| `upstream.py` | Connections to optional upstream STDIO MCP servers |
| `audit.py` | Audit logging and sensitive-data redaction |

All tool execution must pass through `guard.py`. This ensures that built-in tools and upstream tools follow the same approval enforcement mechanism.

## Requirements

- Python 3.12 or newer
- Claude Desktop
- A desktop environment capable of displaying approval popups
- Google Cloud project with Gmail API enabled (for the built-in Gmail integration)
- Google OAuth client of type **Desktop app**

On Linux, the approval popup requires Qt runtime libraries. Depending on the distribution, these may include:

```bash
sudo apt install libgl1 libxkbcommon0 libdbus-1-3
```

## Installation and Setup

### 1. Clone the repository

```bash
git clone <REPOSITORY_URL>
cd MCP-Guardian
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
```

Activate the environment:

**Linux / macOS**

```bash
source .venv/bin/activate
```

**Windows**

```powershell
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

### 3. Configure Gmail OAuth

This step is required only when using the built-in Gmail integration.

**Google Cloud configuration**

1. Open the Google Cloud Console.
2. Create or select a Google Cloud project.
3. Enable the Gmail API.
4. Configure the OAuth consent screen.
5. Under **Audience → Test users**, add the Gmail account you want to authorize.
6. Create an OAuth client with application type **Desktop app**.
7. Copy the generated Client ID and Client Secret.

**Configure environment variables**

Create a `.env` file in the repository root using `.env.example` as a reference.

```env
GMAIL_CLIENT_ID=<your-client-id>
GMAIL_CLIENT_SECRET=<your-client-secret>
```

Keep this file private. Never commit or share your OAuth credentials.

**Authorize Gmail**

With the virtual environment activated, run:

```bash
python -m guardian.gmail_auth
```

Open the authorization URL printed by the command, sign in with your test Gmail account, and approve the requested permissions.

The OAuth token is saved as `token.json` in the repository root, alongside `.env`, with restricted file permissions.

> **Note:** Google OAuth applications in Testing mode generally have refresh tokens that expire after seven days. If the token expires, run the authorization command again.

### 4. Configure Guardian

Create the configuration file:

```bash
cp guardian.config.example.json guardian.config.json
```

On Windows, you can copy the file using:

```powershell
Copy-Item guardian.config.example.json guardian.config.json
```

Configure the following settings:

- `gmail.enabled`: Enables or disables the built-in Gmail integration.
- `gmail.allowed_tools`: Controls which Gmail tools are exposed. The default tools are `search_mails` and `create_draft`.
- `upstreams`: Defines optional upstream STDIO MCP servers and their configurations.

You can leave `upstreams` empty if you only want to use the built-in Gmail tools.

The `.env`, `guardian.config.json`, OAuth token files, and logs should remain excluded from version control.

### 5. Run the tests

Run the test suite before connecting Guardian to an MCP host:

```bash
python -m pytest -v
```

Ensure the tests pass, particularly those covering:

- Approval and denial decisions
- Approval timeouts
- Invalid tool names and arguments
- Popup failures
- Duplicate and stale approval decisions
- Concurrent tool requests
- Fail-closed execution behavior

## Connecting to Claude Desktop

Guardian runs as an MCP server using the STDIO transport.

### 1. Open Claude Desktop configuration

Navigate to:

**Settings → Developer → Edit Config**

### 2. Add the Guardian MCP server

Add the following configuration under the top-level `mcpServers` object:

**Linux / macOS**

```json
{
  "mcpServers": {
    "gmail-guardian": {
      "command": "/absolute/path/to/MCP-Guardian/.venv/bin/python",
      "args": [
        "/absolute/path/to/MCP-Guardian/guardian/main.py"
      ]
    }
  }
}
```

**Windows**

```json
{
  "mcpServers": {
    "gmail-guardian": {
      "command": "C:\\path\\to\\MCP-Guardian\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\path\\to\\MCP-Guardian\\guardian\\main.py"
      ]
    }
  }
}
```

Replace the example paths with the actual absolute paths to your repository.

### 3. Restart Claude Desktop

1. Save the configuration file.
2. Fully quit Claude Desktop, including from the system tray.
3. Reopen Claude Desktop.
4. Verify that Guardian connects successfully.
5. Confirm that the configured tools are available.

Remove any direct integration that exposes the same protected tools without passing through Guardian. Otherwise, those tools could be invoked without Guardian's approval.

## Running Guardian Manually

Claude Desktop normally launches Guardian automatically when it connects to the configured MCP server.

For local debugging or development, you can start Guardian manually.

Activate the virtual environment:

```bash
source .venv/bin/activate
```

Run the application from the repository root:

```bash
python guardian/main.py
```

On Windows, activate the environment using:

```powershell
.venv\Scripts\activate
```

Then run:

```powershell
python guardian/main.py
```

Guardian reads MCP messages from stdin and writes protocol responses to stdout. It is therefore intended to run as an MCP subprocess rather than as a normal interactive terminal application.

Stop manual execution with `Ctrl+C`.

A successful startup should produce a log message similar to:

```text
serving N exposed tools over STDIO
```

**Important:** Stdout must remain reserved for MCP protocol messages. Application logs and OAuth status messages should be written to stderr or the audit log.

## Audit Logging

Guardian records approval decisions and execution outcomes in:

```text
logs/guardian.log
```

Typical audit events include:

```text
approval requested
ALLOWED
BLOCKED
OUTCOME
```

The logs help track:

- Which tool was requested
- Whether approval was granted or denied
- Whether execution succeeded or failed
- Why a request was rejected

Search queries may appear in plain text. Keep audit logs private.

Sensitive fields such as `body` and `content` are redacted, and OAuth tokens are never logged.

## Testing Checklist

Use the following scenarios to validate the complete execution flow:

- [ ] All unit tests pass.
- [ ] Gmail OAuth authorization completes successfully.
- [ ] Guardian starts and exposes the expected tools.
- [ ] **Allow:** A Gmail search displays the approval popup and returns results after approval.
- [ ] **Block:** Blocking a Gmail search prevents execution.
- [ ] **Draft Block:** Blocking a draft request creates nothing in Gmail.
- [ ] **Draft Allow:** Approving a draft request creates a draft without sending an email.
- [ ] **Timeout:** An unanswered approval request is denied after 90 seconds.
- [ ] **Concurrency:** Multiple requests display one popup at a time, with independent approval decisions.
- [ ] **Popup Closed:** Closing the popup while requests are pending denies those requests.
- [ ] **Invalid Tool:** An unknown tool request is rejected.
- [ ] **Invalid Arguments:** Invalid tool arguments are rejected before execution.
- [ ] **Upstream Failure:** An upstream error is handled without bypassing Guardian's approval flow.

## Security Considerations

- **Explicit approval:** Tool execution requires an explicit Allow decision.
- **Fail-closed enforcement:** Errors, timeouts, and popup failures never grant permission.
- **Centralized execution:** All exposed tool calls pass through the same Guardian gateway.
- **Argument validation:** Tool names and arguments are validated before execution.
- **Auditability:** Approval decisions and execution outcomes are logged.
- **Sensitive-data protection:** OAuth tokens are never logged, and sensitive fields are redacted.
- **Least privilege:** The built-in Gmail integration requests read and compose permissions only.
- **No email sending:** Gmail integration currently supports searching emails and creating drafts. Sending emails is not implemented.

Use a dedicated test Gmail account rather than your primary inbox.

Never commit or share:

- `.env`
- `guardian.config.json`
- `token.json`
- OAuth client credentials
- Other OAuth credential files
- `logs/`

## Current Integrations

The current implementation includes:

- **Built-in Gmail integration:** `search_mails` and `create_draft`.
- **Upstream MCP integration:** Connect additional STDIO-based MCP servers and expose their tools through Guardian.

Upstream tools use the naming convention:

```text
<server>__<tool>
```

For example:

```text
filesystem__read_file
github__search_repositories
```

All exposed tools use the same centralized approval and execution flow.

---

## License

Add your chosen open-source license here before publishing the repository.