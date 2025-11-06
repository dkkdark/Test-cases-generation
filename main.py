import json
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama 
from langchain_core.prompts import ChatPromptTemplate
from mcp.server.fastmcp import FastMCP
from testcase_retrieval_tool import GetFullTestCasesTool, CreateTestCaseTool
from prompts import get_test_case_prompt
from vector import Vector
from config import Config
from collections import Counter, defaultdict

load_dotenv(override=True)


class TestCaseService:
    
    def __init__(self):
        self.tool = GetFullTestCasesTool()
        self.create_test_tool = CreateTestCaseTool()
        provider = os.getenv("LLM_PROVIDER", "openai").lower()
        if provider == "ollama":
            self.model = ChatOllama(
                model=os.getenv("OLLAMA_MODEL", "gpt-oss:20b-cloud"),
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            )
        else:
            self.model = ChatOpenAI(
                model='gpt-4o-mini',
                api_key=os.getenv("OPENAI_API")
            )
        self.prompt = ChatPromptTemplate.from_template(get_test_case_prompt())
        self.chain = self.prompt | self.model
        self.vector = Vector()
    
    def _infer_fields_from_similar_questions(self, similar_questions):

        field_values_by_name = defaultdict(list)

        for item in similar_questions or []:
            try:
                doc, _score = item
            except Exception:
                doc = item[0] if isinstance(item, (list, tuple)) and item else item
            content = getattr(doc, "page_content", None)
            if not content or not isinstance(content, str):
                continue

            idx = content.find("Fields:")
            if idx == -1:
                continue

            fields_section = content[idx + len("Fields:"):].strip()
            first_line = fields_section.splitlines()[0] if fields_section else ""
            if not first_line:
                continue

            pairs = [p.strip() for p in first_line.split(";") if p.strip()]
            for pair in pairs:
                if ":" not in pair:
                    continue
                name, value = pair.split(":", 1)
                name = name.strip()
                value = value.strip()
                if name and value:
                    field_values_by_name[name].append(value)

        inferred = []
        for name, values in field_values_by_name.items():
            if not values:
                continue
            most_common_value = Counter(values).most_common(1)[0][0]
            inferred.append({"fieldName": name, "fieldValue": most_common_value})

        order = {"Product": 0, "Epic": 1, "Feature": 2, "Component": 3, "Story": 4}
        sorted_data = sorted(inferred, key=lambda x: order.get(x["fieldName"], 999))

        return sorted_data

    def get_test_case(self, query: str) -> str:
        try:
            vectorstore = self.vector.get_db()
            similar_questions = vectorstore.similarity_search_with_score(query, k=10)
            fd = vectorstore.similarity_search_with_vectors(query, k=10)
            inferred_fields = self._infer_fields_from_similar_questions(similar_questions)
            result = self.chain.invoke({"query": query, "data": similar_questions, "fields": inferred_fields}).content
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
            if os.getenv("EMBEDDINGS_PROVIDER") == "ollama":
                self.save_allure_test_cases()
                return "Test cases loaded and saved successfully!"
            return "Test cases loaded successfully!"
        except Exception as e:
            return f"Error: {str(e)}"
    
    def save_allure_test_cases(self) -> str:
        try:
            _ = self.vector.add_testcases()
            return "Test cases were successfully added to vector database!"
        except Exception as e:
            print(f"Error saving test cases: {e}")
            return f"Error: {str(e)}"
    
    def create_test_case(self, name: str, precondition: str, steps: list[str], expected_result: str, fields: list[dict]) -> str:
        try:
            self.create_test_tool.run(tool_input={
                "name": name,
                "precondition": precondition,
                "steps": steps,
                "expected_result": expected_result,
                "fields": fields,
            })
            return "Test case was successfully created!"
        except Exception as e:
            print(f"Error creating test case: {e}")
            return f"Error: {str(e)}"


mcp = FastMCP("Create TestCases")
test_case_service = TestCaseService()


@mcp.tool()
def get_test_case(query: str) -> str:
    return test_case_service.get_test_case(query)


@mcp.tool()
def load_test_cases_from_allure() -> str:
    return test_case_service.load_test_cases_from_allure()


@mcp.tool()
def save_allure_test_cases() -> str:
    return test_case_service.save_allure_test_cases()


@mcp.tool()
def create_test_case(name: str, precondition: str, steps: list[str], expected_result: str, fields: list[dict]) -> str:
    return test_case_service.create_test_case(
        name=name,
        precondition=precondition,
        steps=steps,
        expected_result=expected_result,
        fields=fields,
    )


if __name__ == "__main__":
    mcp.run(transport=Config.Server.TRANSPORT)