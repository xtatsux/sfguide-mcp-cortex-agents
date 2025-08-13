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
        'SEMANTIC_MODEL_FILE': os.getenv("SEMANTIC_MODEL_FILE"),
        'CORTEX_SEARCH_SERVICE': os.getenv("CORTEX_SEARCH_SERVICE"),
        'SNOWFLAKE_ACCOUNT_URL': os.getenv("SNOWFLAKE_ACCOUNT_URL"),
        'SNOWFLAKE_PAT': os.getenv("SNOWFLAKE_PAT")
    }

# Initial load for backward compatibility
SEMANTIC_MODEL_FILE = os.getenv("SEMANTIC_MODEL_FILE")
CORTEX_SEARCH_SERVICE = os.getenv("CORTEX_SEARCH_SERVICE")
SNOWFLAKE_ACCOUNT_URL = os.getenv("SNOWFLAKE_ACCOUNT_URL")
SNOWFLAKE_PAT = os.getenv("SNOWFLAKE_PAT")

if not SNOWFLAKE_PAT:
    raise RuntimeError("Set SNOWFLAKE_PAT environment variable")
if not SNOWFLAKE_ACCOUNT_URL:
    raise RuntimeError("Set SNOWFLAKE_ACCOUNT_URL environment variable")

# Headers for API requests
API_HEADERS = {
    "Authorization": f"Bearer {SNOWFLAKE_PAT}",
    "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
    "Content-Type": "application/json",
}

async def process_sse_response(resp: httpx.Response) -> Tuple[str, str, List[Dict]]:
    """
    Process SSE stream lines, extracting any 'delta' payloads,
    regardless of whether the JSON contains an 'event' field.
    """
    text, sql, citations = "", "", []
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
                        # capture SQL if present
                        if "sql" in j:
                            sql = j["sql"]
                        # capture any citations
                        for s in j.get("searchResults", []):
                            citations.append({
                                "source_id": s.get("source_id"),
                                "doc_id": s.get("doc_id"),
                            })
    return text, sql, citations

async def execute_sql(sql: str) -> Dict[str, Any]:
    """Execute SQL using the Snowflake SQL API.
    
    Args:
        sql: The SQL query to execute
        
    Returns:
        Dict containing either the query results or an error message
    """
    try:
        # Generate a unique request ID
        request_id = str(uuid.uuid4())
        
        # Prepare the SQL API request
        sql_api_url = f"{SNOWFLAKE_ACCOUNT_URL}/api/v2/statements"
        sql_payload = {
            "statement": sql.replace(";", ""),
            "timeout": 60  # 60 second timeout
        }
        
        async with httpx.AsyncClient() as client:
            sql_response = await client.post(
                sql_api_url,
                json=sql_payload,
                headers=API_HEADERS,
                params={"requestId": request_id}
            )
            
            if sql_response.status_code == 200:
                return sql_response.json()
            else:
                return {"error": f"SQL API error: {sql_response.text}"}
    except Exception as e:
        return {"error": f"SQL execution error: {e}"}

import uuid
import httpx

@mcp.tool()
async def run_cortex_agents(query: str) -> Dict[str, Any]:
    """Run the Cortex agent with the given query, streaming SSE."""
    # Reload environment variables dynamically
    env_vars = get_env_vars()
    
    # Debug: Print environment variables
    print(f"DEBUG - SEMANTIC_MODEL_FILE: {env_vars['SEMANTIC_MODEL_FILE']}")
    print(f"DEBUG - CORTEX_SEARCH_SERVICE: {env_vars['CORTEX_SEARCH_SERVICE']}")
    print(f"DEBUG - SNOWFLAKE_ACCOUNT_URL: {env_vars['SNOWFLAKE_ACCOUNT_URL']}")
    print(f"DEBUG - SNOWFLAKE_PAT exists: {bool(env_vars['SNOWFLAKE_PAT'])}")
    
    # Build your payload exactly as before
    payload = {
        "model": "claude-3-5-sonnet",
        "response_instruction": "You are a helpful AI assistant.",
        "experimental": {},
        "tools": [
            {"tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "Analyst1"}},
            {"tool_spec": {"type": "cortex_search",            "name": "Search1"}},
            {"tool_spec": {"type": "sql_exec",                "name": "sql_execution_tool"}},
        ],
        "tool_resources": {
            "Analyst1": {"semantic_model_file": env_vars['SEMANTIC_MODEL_FILE']},
            "Search1":  {"name": env_vars['CORTEX_SEARCH_SERVICE']},
        },
        "tool_choice": {"type": "auto"},
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": query}]}
        ],
    }
    
    print(f"DEBUG - Payload: {payload}")

    # (Optional) generate a request ID if you want traceability
    request_id = str(uuid.uuid4())

    url = f"{env_vars['SNOWFLAKE_ACCOUNT_URL']}/api/v2/cortex/agent:run"
    # Copy your API headers and add the SSE Accept
    headers = {
        "Authorization": f"Bearer {env_vars['SNOWFLAKE_PAT']}",
        "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    # 1) Open a streaming POST
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
                params={"requestId": request_id},   # SQL API needs this, Cortex agent may ignore it
            ) as resp:
                print(f"DEBUG - Response status: {resp.status_code}")
                if resp.status_code != 200:
                    error_text = await resp.aread()
                    print(f"DEBUG - Error response: {error_text.decode()}")
                    return {"error": f"HTTP {resp.status_code}: {error_text.decode()}"}
                
                # 2) Now resp.aiter_lines() will yield each "data: …" chunk
                text, sql, citations = await process_sse_response(resp)
    except Exception as e:
        print(f"DEBUG - Exception occurred: {e}")
        return {"error": f"Request failed: {e}"}

    # 3) If SQL was generated, execute it
    results = await execute_sql(sql) if sql else None

    return {
        "text": text,
        "citations": citations,
        "sql": sql,
        "results": results,
    }


if __name__ == "__main__":
    mcp.run(transport='stdio')