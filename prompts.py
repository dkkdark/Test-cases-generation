def get_test_case_prompt() -> str:
    return """
        You are an expert at test case generation.

        Based on the following documentation or user request:
        ---
        {query}
        ---

        Create a **minimal set of test cases** to cover only the most critical scenarios.

        Rely on the style and structure of similar tests in the database:
        ---
        {data}
        ---

        CRITICALLY IMPORTANT:
        - Create ONLY the most basic tests for key scenarios. If one test is possible, then create only one test with different conditions in Step.
        - DO NOT try to cover all the details in the documentation.
        - DO NOT create tests for edge cases, validation, or error handling.
        - One test = one core feature/function.
        - Strictly follow the style of the tests in the database. Estimate the length and word count of the test in each section and follow a roughly similar structure.

        Result format (JSON):
        [
        {{
        "Name": string
        "Precondition": string (only the most critical conditions)
        "Step": list(string) (if needed, you can write the expected result and some additional conditions here)
        "Expected result": string (short expected result, can be omitted if previously described)
        "Fields": list(dict(string)) (take unchanged from {fields})
        }}
        ]
    """