# Build an MCP Server for Cortex Agent

This guide walks through how to build your own Cortex Agent MCP Server.

The core functionalities include:

- Allow agents to interact with Cortex Agent as a tool
- Test the connection with Claude Desktop

In this tutorial, we’ll build a simple MCP **Cortex Agent** server and connect it to an MCP host (Claude for Desktop). We’ll start with a basic setup, then progress to more complex use cases.

## What we’ll be building

Many LLMs don’t natively orchestrate external “agent” workflows. With MCP, we can expose Cortex Agent capabilities as first-class tools in your chat client.

We’ll build a server that exposes one tool:

- `cortex_agent`: submit a prompt to Cortex Agent and get its output  

Then we’ll connect the server to an MCP host (Claude for Desktop):

> **Note:** you can connect any MCP-compatible client, but this guide uses Claude for Desktop for simplicity. See the official MCP [client SDK guide](#) for building your own, or browse [other clients](https://modelcontextprotocol.io/clients).

## Prerequisite knowledge

- Cortex Analyst semantic model and Cortex Search service created, such as via this ([quickstart](https://quickstarts.snowflake.com/guide/getting_started_with_cortex_agents/index.html#0))
- Basic familiarity with LLMs

## System requirements

- Python **3.10+**  
- Python MCP SDK **1.2.0+**  

---

## 1. Set up your environment

First install the MCP CLI (`uv`) and bootstrap your project:

### macOS / Linux

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# restart your terminal to pick up `uv`
```

### Windows

```bash
irm https://astral.sh/uv/install.ps1 | iex
# restart your shell
```

Configure and create your Cortex Agent project:

```bash
# Clone the repo
git clone https://github.com/Snowflake-Labs/sfguide-mcp-cortex-agent.git
cd sguide-mcp-cortex-agent

# Create and activate venv
uv venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\Activate.ps1 # Windows PowerShell

# Install MCP SDK and HTTP client
uv add "mcp[cli]" httpx
```

Set the keys and services needed to run Cortex Agent by filling `.env.template` with:

* SNOWFLAKE_ACCOUNT_URL
* SNOWFLAKE_PAT
* SEMANTIC_MODEL_FILE
* CORTEX_SEARCH_SERVICE

2. Run the server

3. (Optional) Use the Cortex Agent MCP Server in Claude Desktop:

Install or update Claude for Desktop.

Open your config file:

macOS: ~/Library/Application Support/Claude/claude_desktop_config.json

Windows: %APPDATA%\Claude\claude_desktop_config.json

Add your Cortex Agent server:

```json
{
  "mcpServers": {
    "cortex-agent": {x
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/PARENT/FOLDER/sfguide-mcp-cortex-agent",
        "run",
        "cortex_agent.py"
      ]
    }
  }
}
```

> **Note:** You may need to replace "uv" with the full uv path. You can find the uv path by running `which uv`.

Launch the Claude for Desktop app.

Then, run a query. If the query calls your MCP server, you will see the name of the tool used directly below your query in the Claude desktop app.