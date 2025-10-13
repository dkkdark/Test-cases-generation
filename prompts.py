def get_test_case_prompt() -> str:
    return """
        You are an expert in generating test cases.

Based on the following documentation or user request:
---
{query}
---

Create a **minimal set of test cases** to cover only the most critical scenarios.

        Refer to the style and structure of similar tests from the database:
---
{data}
---

CRITICAL:
- Create ONLY the most basic tests for key scenarios. If one test is sufficient, create only one test with different conditions in Step.
- DO NOT try to cover all the details from the documentation.
- DO NOT create tests for edge cases, validation, or error handling.
- One test = one main feature/function.
- Strictly follow the style of tests from the database. Estimate the length and number of words in the test in each section and follow approximately the same structure.

Result format (JSON):
[
{{
“Name”: string
“Precondition”: string (only the most critical conditions)
        “Step”: list(string) (if necessary, you can write the expected result and some additional conditions here)
        “Expected result”: string (brief expected result, can be omitted if already described earlier)
        }}
        ]

Translated with DeepL.com (free version)
    """