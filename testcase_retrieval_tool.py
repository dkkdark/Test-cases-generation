from typing import Dict, Any, List, Optional
import os
import requests
from langchain.tools import BaseTool
import json
from dotenv import load_dotenv

load_dotenv(override=True)

class _JWTAuthTool(BaseTool):

    base_url: str = os.getenv("BASE_URL")
    user_token: Optional[str] = None

    _jwt_token: Optional[str] = None
    _verify_ssl: bool = False

    def _fetch_jwt_token(self) -> str:
        if self._jwt_token:
            return self._jwt_token

        token_source = self.user_token or os.getenv("USER_TOKEN")
        if not token_source:
            raise ValueError("USER_TOKEN is not provided (env USER_TOKEN or tool.user_token)")

        url = f"{self.base_url}/api/uaa/oauth/token"
        data = {
            "grant_type": "apitoken",
            "scope": "openid",
            "token": token_source,
        }
        headers = {"Accept": "application/json"}
        resp = requests.post(url, headers=headers, data=data, verify=self._verify_ssl)
        resp.raise_for_status()
        access_token = (resp.json() or {}).get("access_token")
        if not access_token:
            raise RuntimeError("Failed to obtain access_token from auth response")
        self._jwt_token = access_token
        return access_token

    def _get_headers(self) -> Dict[str, str]:
        base_headers: Dict[str, str] = dict({})
        if "Authorization" not in base_headers:
            jwt = self._fetch_jwt_token()
            base_headers["Authorization"] = f"Bearer {jwt}"
        if "Accept" not in base_headers:
            base_headers["Accept"] = "application/json"
        return base_headers


class GetFullTestCasesTool(_JWTAuthTool):

    name: str = "get_full_testcases"
    description: str = (
        "Fetch test cases info and their scenario steps from the API"
    )

    _verify_ssl: bool = False

    def _fetch_all_testcases(self, size: int) -> List[Dict[str, Any]]:
        all_testcases = []

        url = f"{self.base_url}/api/testcase/__search?page=0&size={size}&projectId=2&rql=status%3D%22Active%22%20and%20cfv%20in%20%5B%22%D0%91%D0%B0%D0%BD%D0%BA%D0%B8.%D1%80%D1%83_IOS_%D1%80%D1%83%D1%87%D0%BD%D1%8B%D0%B5%22%5D%20and%20not%20tag%20%3D%20%22automated_by_units%22"
        first_resp = requests.get(url, headers=self._get_headers(), verify=False)
        first_resp.raise_for_status()
        first_data = first_resp.json()

        total_pages = first_data.get("totalPages", 1)

        for page in range(0, total_pages + 1):
            url = f"{self.base_url}/api/testcase/__search?page={page}&size={size}&projectId=2&rql=status%3D%22Active%22%20and%20cfv%20in%20%5B%22%D0%91%D0%B0%D0%BD%D0%BA%D0%B8.%D1%80%D1%83_IOS_%D1%80%D1%83%D1%87%D0%BD%D1%8B%D0%B5%22%5D%20and%20not%20tag%20%3D%20%22automated_by_units%22"

            resp = requests.get(url, headers=self._get_headers(), verify=False)
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
        resp = requests.get(url, headers=self._get_headers(), verify=False)
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
        resp = requests.get(url, headers=self._get_headers(), verify=False)
        resp.raise_for_status()
        data = resp.json()
        scenario_steps = data.get("scenarioSteps", {})
        return [s.get("body") for s in scenario_steps.values() if s.get("body")]

    def _fetch_testcase_fields(self, testcase_id: int) -> List[Dict[str, Optional[str]]]:
        url = f"{self.base_url}/api/testcase/{testcase_id}/cfv?projectId=2"
        resp = requests.get(url, headers=self._get_headers(), verify=False)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "fieldValue": field.get("name"),
                "fieldName": (field.get("customField") or {}).get("name"),
            }
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


class CreateTestCaseTool(_JWTAuthTool):

    name: str = "create_allure_testcase"
    description: str = (
        "Create a new test case in Allure using name, precondition, steps, expected result, and custom fields."
    )

    _verify_ssl: bool = True

    def _get_fields(self):
        ids_to_query = {
            "Epic": -1,
            "Issue": 17,
            "Feature": -2,
            "Story": -3,
            "Component": -4
        }

        fields_data = {}

        for field_name, field_id in ids_to_query.items():
            response = requests.get(url=f"{self.base_url}/api/project/2/cfv?customFieldId={field_id}&size=350", headers=self._get_headers())
            if response.status_code == 200:
                data = response.json()
                extracted_fields = []
                
                for item in data.get("content", []):
                    extracted_fields.append({
                        "id": item.get("id"),
                        "name": item.get("name")
                    })
                
                fields_data[field_name] = extracted_fields
            else:
                print(f"Failed to fetch {field_name} with ID {field_id}")
        
        return fields_data

    def _build_body_json(self, text: str) -> Dict[str, Any]:
        return {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                        }
                    ],
                }
            ],
        }

    def _create_scenario_step(self, text: str, id: int) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/api/testcase/step"
        payload = {
            "bodyJson": self._build_body_json(text),
            "testCaseId": id
        }
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            print(f"Failed to create scenario step for text '{text}': {exc}")
            return None

    def _create_scenario(self, steps: Optional[List[str]], id: int) -> List[Dict[str, Any]]:
        created: List[Dict[str, Any]] = []
        for s in steps or []:
            if not s:
                continue
            result = self._create_scenario_step(str(s), id)
            if result is not None:
                created.append(result)
        return created

    def _build_payload(
        self,
        name: str,
        precondition: Optional[str],
        expected_result: Optional[str],
        fields: Optional[List[Dict[str, Any]]],
        project_id: Optional[int] = 2,
    ) -> Dict[str, Any]:
        custom_fields_input = fields or []
        custom_fields_input = [field for field in custom_fields_input if field.get("fieldName") != "Product"]

        available_fields = self._get_fields()
        
        custom_fields: List[Dict[str, Any]] = []
        custom_fields.append({
                "customField": {
                    "id": 9
                },
                "id": 10152,
            })
        for f in custom_fields_input:
            field_name = f.get("fieldName") or f.get("name")
            field_value = f.get("fieldValue") or f.get("value")
            if field_name is None or field_value is None:
                continue
            
            field_id = None
            value_id = None
            
            ids_to_query = {
                "Epic": -1,
                "Issue": 17,
                "Feature": -2,
                "Story": -3,
                "Component": -4
            }
            
            target_category = None
            for category in ids_to_query.keys():
                if category == field_name:
                    target_category = category
                    break
            
            if target_category is None:
                raise ValueError(f"Field name '{field_name}' not found in available categories")
            
            field_id = ids_to_query.get(target_category)
            value_id = None
            
            if target_category in available_fields:
                for field_info in available_fields[target_category]:
                    if field_info["name"] == field_value:
                        value_id = field_info["id"]
                        break
            
            if value_id is None:
                raise ValueError(f"Field value '{field_value}' not found in category '{field_name}'")
            
            custom_fields.append({
                "customField": {
                    "id": field_id
                },
                "id": value_id,
            })

        print(f"custom_fields {custom_fields}")

        payload: Dict[str, Any] = {
            "automated": True,
            "name": name,
            "precondition": precondition or "",
            "expectedResult": expected_result or "",
            "customFields": custom_fields
        }

        if project_id is not None:
            payload["projectId"] = project_id

        return payload

    def _run(self, **kwargs):
        name: str = kwargs.get("name")
        if not name:
            raise ValueError("'name' is required")

        precondition: Optional[str] = kwargs.get("precondition")
        steps: Optional[List[str]] = kwargs.get("steps")
        expected_result: Optional[str] = kwargs.get("expected_result") or kwargs.get("expectedResult")
        fields: Optional[List[Dict[str, Any]]] = kwargs.get("fields")
        project_id: Optional[int] = kwargs.get("project_id") or kwargs.get("projectId") or 2

        payload = self._build_payload(
            name=name,
            precondition=precondition,
            expected_result=expected_result,
            fields=fields,
            project_id=project_id,
        )

        url = f"{self.base_url}/api/testcase"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        resp = requests.post(url, headers=self._get_headers(), json=payload)
        resp.raise_for_status()
        print(resp.json())

        if resp.status_code == 200:
            try:
                id = resp.json()["id"]
                self._create_scenario(steps, id)
            except Exception as exc:
                print(f"Scenario creation failed: {exc}")

    async def _arun(self, tool_input: Any):
        raise NotImplementedError("Async not supported.")
