import json
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from mcp.server.fastmcp import FastMCP
from testcase_retrieval_tool import GetFullTestCasesTool
from prompts import get_test_case_prompt
from vector import Vector
from vars import BASE_URL, HEADERS
from config import Config

load_dotenv()


class TestCaseService:
    
    def __init__(self):
        self.tool = GetFullTestCasesTool(base_url=BASE_URL, headers=HEADERS)
        self.model = ChatOpenAI(model='gpt-4.1-mini', api_key=os.getenv("OPENAI_API"))
        self.prompt = ChatPromptTemplate.from_template(get_test_case_prompt())
        self.chain = self.prompt | self.model
        self.vector = Vector()
    
    def get_test_case(self, query: str) -> str:
        try:
            vectorstore = self.vector.get_db()
            similar_questions = vectorstore.similarity_search_with_score(query, k=10)
            result = self.chain.invoke({"query": query, "data": similar_questions}).content
            print("Generated test case result:")
            print(result)
            return result
        except Exception as e:
            return f"Error: {str(e)}"
    
    def load_test_cases_from_allure(self, size: int = 20) -> str:
        try:
            test_cases = self.tool.run(tool_input={"size": size})
            with open("testcases.json", "w", encoding="utf-8") as f:
                json.dump(test_cases, f, ensure_ascii=False, indent=2)
            return "Test cases loaded and saved successfully!"
        except Exception as e:
            return f"Error: {str(e)}"
    
    def save_allure_test_cases(self) -> str:
        try:
            _ = self.vector.add_testcases()
            return "Test cases were successfully added to vector database!"
        except Exception as e:
            print(f"Error saving test cases: {e}")
            return f"Error: {str(e)}"


mcp = FastMCP("Create TestCases")
test_case_service = TestCaseService()


@mcp.tool()
def get_test_case(query: str) -> str:
    return test_case_service.get_test_case(query)


@mcp.tool()
def load_test_case_from_allure() -> str:
    return test_case_service.load_test_cases_from_allure()


@mcp.tool()
def save_allure_test_cases() -> str:
    return test_case_service.save_allure_test_cases()


if __name__ == "__main__":
    mcp.run(transport=Config.Server.TRANSPORT)