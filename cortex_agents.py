from typing import Any, Dict, Tuple, List, Optional
import httpx
from mcp.server.fastmcp import FastMCP
import os
import json
import uuid
from dotenv import load_dotenv, find_dotenv
import asyncio
load_dotenv(find_dotenv())

# Initialize FastMCP server
mcp = FastMCP("cortex_agent")

# Constants - reload environment variables each time
def get_env_vars():
    load_dotenv(find_dotenv())  # Reload environment variables
    return {
        'CORTEX_SEARCH_SERVICE': os.getenv("CORTEX_SEARCH_SERVICE"),
        'SNOWFLAKE_ACCOUNT_URL': os.getenv("SNOWFLAKE_ACCOUNT_URL"),
        'SNOWFLAKE_PAT': os.getenv("SNOWFLAKE_PAT")
    }

# Initial load for backward compatibility
CORTEX_SEARCH_SERVICE = os.getenv("CORTEX_SEARCH_SERVICE")
SNOWFLAKE_ACCOUNT_URL = os.getenv("SNOWFLAKE_ACCOUNT_URL")
SNOWFLAKE_PAT = os.getenv("SNOWFLAKE_PAT")

if not SNOWFLAKE_PAT:
    raise RuntimeError("Set SNOWFLAKE_PAT environment variable")
if not SNOWFLAKE_ACCOUNT_URL:
    raise RuntimeError("Set SNOWFLAKE_ACCOUNT_URL environment variable")
if not CORTEX_SEARCH_SERVICE:
    raise RuntimeError("Set CORTEX_SEARCH_SERVICE environment variable")

# Headers for API requests
API_HEADERS = {
    "Authorization": f"Bearer {SNOWFLAKE_PAT}",
    "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
    "Content-Type": "application/json",
}

async def process_sse_response(resp: httpx.Response) -> Tuple[str, str, List[Dict]]:
    """
    Process SSE stream lines for Cortex Search responses,
    extracting text and citations only.
    """
    text, citations = "", []
    async for raw_line in resp.aiter_lines():
        if not raw_line:
            continue
        raw_line = raw_line.strip()
        # only handle data lines
        if not raw_line.startswith("data:"):
            continue
        payload = raw_line[len("data:") :].strip()
        if payload in ("", "[DONE]"):
            continue
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        # Grab the 'delta' section, whether top-level or nested in 'data'
        delta = evt.get("delta") or evt.get("data", {}).get("delta")
        if not isinstance(delta, dict):
            continue
        for item in delta.get("content", []):
            t = item.get("type")
            if t == "text":
                text += item.get("text", "")
            elif t == "tool_results":
                for result in item["tool_results"].get("content", []):
                    if result.get("type") == "json":
                        j = result["json"]
                        text += j.get("text", "")
                        # capture any citations from search results
                        for s in j.get("searchResults", []):
                            citations.append({
                                "source_id": s.get("source_id"),
                                "doc_id": s.get("doc_id"),
                            })
    return text, "", citations

@mcp.tool()
async def run_cortex_search(query: str) -> Dict[str, Any]:
    """Run Cortex Search with the given query."""
    # Reload environment variables dynamically
    env_vars = get_env_vars()
    
    # Build simplified payload for Cortex Search only
    payload = {
        "model": "claude-3-5-sonnet",
        "response_instruction": "You are a helpful search assistant.",
        "tools": [
            {"tool_spec": {"type": "cortex_search", "name": "Search1"}}
        ],
        "tool_resources": {
            "Search1": {"name": env_vars['CORTEX_SEARCH_SERVICE']}
        },
        "tool_choice": {"type": "auto"},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": query}]}
        ],
    }
    
    # Generate a request ID for traceability
    request_id = str(uuid.uuid4())

    url = f"{env_vars['SNOWFLAKE_ACCOUNT_URL']}/api/v2/cortex/agent:run"
    headers = {
        "Authorization": f"Bearer {env_vars['SNOWFLAKE_PAT']}",
        "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    # Open a streaming POST request
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
                params={"requestId": request_id},
            ) as resp:
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    return {"error": f"HTTP {resp.status_code}: {error_text.decode()}"}
                
                # Process the SSE response
                text, sql, citations = await process_sse_response(resp)
    except Exception as e:
        return {"error": f"Request failed: {e}"}

    return {
        "text": text,
        "citations": citations,
    }


if __name__ == "__main__":
    mcp.run(transport='stdio')