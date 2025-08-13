from typing import Any, Dict, Tuple, List, Optional
import httpx
from mcp.server.fastmcp import FastMCP
import os
import json
import uuid
import re
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

def detect_japanese(text: str) -> bool:
    """
    Detect if text contains Japanese characters (Hiragana, Katakana, Kanji).
    
    Args:
        text: Input text to check
        
    Returns:
        True if Japanese characters are found, False otherwise
    """
    japanese_pattern = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]')
    return bool(japanese_pattern.search(text))

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
async def get_search_guidance(user_query: str) -> Dict[str, str]:
    """
    Provide guidance for optimal Cortex Search usage, especially for non-English queries.
    
    This helper tool suggests how to optimize queries for better search results.
    
    Args:
        user_query: The original user query in any language
        
    Returns:
        Dict containing optimization suggestions and translation guidance
    """
    return {
        "original_query": user_query,
        "guidance": "For optimal search results with Cortex Search:",
        "steps": [
            "1. If the query is in Japanese or non-English, translate it to English first",
            "2. Use specific technical terms and clear language",
            "3. Focus on key concepts (e.g., 'security', 'performance', 'configuration')",
            "4. Call run_cortex_search with the English version"
        ],
        "example": "Query: 'Snowflakeの動的テーブル' → Translate to: 'Snowflake Dynamic Tables' → Better results",
        "note": "The MCP client (Claude) can automatically handle translation - just call run_cortex_search with English queries for best results"
    }

@mcp.tool()
async def run_cortex_search(query: str) -> Dict[str, Any]:
    """
    Run Cortex Search to find information from Snowflake documentation.
    
    🚨 LANGUAGE REQUIREMENT: This tool ONLY accepts English queries.
    
    🔄 TRANSLATION PROTOCOL:
    If you receive a Japanese query, you MUST:
    1. Detect Japanese characters in the query
    2. Translate the query to English using your language capabilities  
    3. Call this tool again with the English translation
    
    ❌ Japanese queries will be REJECTED with translation instructions.
    ✅ Only English queries will be processed for search.
    
    💡 WORKFLOW EXAMPLE:
    - User asks: "Snowflakeのセキュリティについて教えて"
    - First call: run_cortex_search("Snowflakeのセキュリティについて教えて") → TRANSLATION_REQUIRED
    - You translate: "Tell me about Snowflake security"
    - Second call: run_cortex_search("Tell me about Snowflake security") → Search results
    - You respond: Provide comprehensive answer in Japanese
    
    Args:
        query: Search query (MUST be in English)
        
    Returns:
        Dict containing search results OR translation requirement
    """
    
    # 日本語検出 - 日本語が含まれている場合は翻訳を要求
    if detect_japanese(query):
        return {
            "status": "TRANSLATION_REQUIRED",
            "error": "Japanese query detected - English translation required",
            "original_query": query,
            "instruction": "Please translate this Japanese query to English and call run_cortex_search again with the English version.",
            "example": f"Original: '{query}' → Translate to English → Call run_cortex_search('English translation')",
            "note": "This tool only processes English queries for optimal search results."
        }
    
    # 英語クエリの場合のみ検索実行
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