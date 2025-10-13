from typing import Dict, Any, List, Optional
import requests
from langchain.tools import BaseTool


class GetFullTestCasesTool(BaseTool):

    name: str = "get_full_testcases"
    description: str = (
        "Fetch test cases info and their scenario steps from the API"
    )

    base_url: str
    headers: Optional[Dict[str, str]] = None

    def _fetch_all_testcases(self, size: int) -> List[Dict[str, Any]]:
        all_testcases = []

        url = f"{self.base_url}/api/testcase/__search?page=0&size={size}&projectId=2&rql=status%3D%22Active%22%20and%20cfv%20in%20%5B%22%D0%91%D0%B0%D0%BD%D0%BA%D0%B8.%D1%80%D1%83_IOS_%D1%80%D1%83%D1%87%D0%BD%D1%8B%D0%B5%22%5D%20and%20not%20tag%20%3D%20%22automated_by_units%22"
        first_resp = requests.get(url, headers=self.headers, verify=False)
        first_resp.raise_for_status()
        first_data = first_resp.json()

        total_pages = first_data.get("totalPages", 1)

        for page in range(0, total_pages + 1):
            url = f"{self.base_url}/api/testcase/__search?page={page}&size={size}&projectId=2&rql=status%3D%22Active%22%20and%20cfv%20in%20%5B%22%D0%91%D0%B0%D0%BD%D0%BA%D0%B8.%D1%80%D1%83_IOS_%D1%80%D1%83%D1%87%D0%BD%D1%8B%D0%B5%22%5D%20and%20not%20tag%20%3D%20%22automated_by_units%22"

            resp = requests.get(url, headers=self.headers, verify=False)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", [])

            all_testcases.extend([
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "precondition": item.get("precondition"),
                    "expectedResult": item.get("expectedResult"),
                }
                for item in content
            ])

        return all_testcases


    def _fetch_testcases_page(self, page: int, size: int) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/testcase/__search?page={page}&size={size}&projectId=2&rql=status%3D%22Active%22%20and%20cfv%20in%20%5B%22%D0%91%D0%B0%D0%BD%D0%BA%D0%B8.%D1%80%D1%83_IOS_%D1%80%D1%83%D1%87%D0%BD%D1%8B%D0%B5%22%5D%20and%20not%20tag%20%3D%20%22automated_by_units%22"
        resp = requests.get(url, headers=self.headers, verify=False)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", [])
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "precondition": item.get("precondition"),
                "expectedResult": item.get("expectedResult"),
            }
            for item in content
        ]

    def _fetch_testcase_steps(self, testcase_id: int) -> List[str]:
        url = f"{self.base_url}/api/testcase/{testcase_id}/step"
        resp = requests.get(url, headers=self.headers, verify=False)
        resp.raise_for_status()
        data = resp.json()
        scenario_steps = data.get("scenarioSteps", {})
        return [s.get("body") for s in scenario_steps.values() if s.get("body")]

    def _fetch_testcase_fields(self, testcase_id: int) -> List[str]:
        url = f"{self.base_url}/api/testcase/{testcase_id}/cfv?projectId=2"
        resp = requests.get(url, headers=self.headers, verify=False)
        resp.raise_for_status()
        data = resp.json()
        return [
            field.get("name")
            for field in data
        ]

    def _run(self, **kwargs) -> List[Dict[str, Any]]:

        size = kwargs.get("size", 20)

        all_testcases = []
        cases = self._fetch_all_testcases(size)
        for case in cases:
            try:
                steps = self._fetch_testcase_steps(case["id"])
                case["steps"] = steps
            except Exception as e:
                case["steps"] = []
                case["error"] = str(e)

            try:
                case["fields"] = self._fetch_testcase_fields(case["id"])
            except Exception as e:
                case["fields"] = []
                case["fields_error"] = str(e)

        all_testcases.extend(cases)
        return all_testcases

    async def _arun(self, tool_input: Any):
        raise NotImplementedError("Async not supported.")
