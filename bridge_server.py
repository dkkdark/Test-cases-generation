import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict
from config import connect_to_server 
import traceback

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helper function to call MCP tools
async def call_tool(tool_name: str, args: dict = None, query_text: str = ""):
    try:
        async with connect_to_server() as session:
            if tool_name == "get_test_case":
                result = await session.call_tool("get_test_case", arguments={"query": query_text})
                return result.structuredContent.get("result")

            elif tool_name == "load_test_cases_from_allure":
                result = await session.call_tool("load_test_cases_from_allure", arguments={})
                return result.structuredContent.get("result")

            elif tool_name == "save_allure_test_cases":
                result = await session.call_tool("save_allure_test_cases", arguments={})
                return result.structuredContent.get("result")

            elif tool_name == "create_test_case":
                args = args or {}
                result = await session.call_tool("create_test_case", arguments=args)
                return result.structuredContent.get("result")
    except Exception as e:
        print(f"call_tool error for {tool_name}: {e}")
        print(traceback.format_exc())
        raise e

@app.post("/get_test_case")
async def get_test_case(request: Request):
    data: Dict = await request.json()
    query_text = data.get("query", "")
    try:
        result = await call_tool("get_test_case", query_text=query_text)
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/load_test_cases_from_allure")
async def load_test_cases_from_allure():
    try:
        result = await call_tool("load_test_cases_from_allure")
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/save_allure_test_cases")
async def save_allure_test_cases():
    try:
        result = await call_tool("save_allure_test_cases")
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/create_test_case")
async def create_test_case(request: Request):
    data: Dict = await request.json()
    args = {
        "name": data.get("name", ""),
        "precondition": data.get("precondition", ""),
        "steps": data.get("steps", []),
        "expected_result": data.get("expected_result", ""),
        "fields": data.get("fields", [])
    }
    try:
        result = await call_tool("create_test_case", args=args)
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}
