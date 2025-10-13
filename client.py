import asyncio
import streamlit as st
import json
from config import connect_to_server

st.title("Test Case Creation Tool")

def init_session_state():
    if 'test_case_data' not in st.session_state:
        st.session_state.test_case_data = None
    if 'editable_fields' not in st.session_state:
        st.session_state.editable_fields = {}
    if 'test_cases_list' not in st.session_state:
        st.session_state.test_cases_list = []
    if 'current_test_case_index' not in st.session_state:
        st.session_state.current_test_case_index = 0

init_session_state()

async def call_tool(tool_name: str, query_text: str = ""):
    try:
        async with connect_to_server() as session:
            if tool_name == "get_test_case":
                result = await session.call_tool(
                    "get_test_case",
                    arguments={"query": query_text}
                )
                return result.structuredContent.get("result")

            elif tool_name == "load_test_case_from_allue":
                result = await session.call_tool("load_test_case_from_allue", arguments={})
                return result.structuredContent.get("result")

            elif tool_name == "save_allure_test_cases":
                result = await session.call_tool("save_allure_test_cases", arguments={})
                return result.structuredContent.get("result")
    except Exception as e:
        raise e

with st.sidebar:
    st.header("Additional Actions")
    
    if st.button("Load Allure test cases"):
        with st.spinner("Loading..."):
            res = asyncio.run(call_tool("load_test_case_from_allue"))
            st.write(res)
    
    if st.button("Save Allure test cases to DB"):
        with st.spinner("Saving..."):
            res = asyncio.run(call_tool("save_allure_test_cases"))
            st.write(res)

with st.form("test_case_form"):
    query = st.text_input("Enter your query to create a test case:", placeholder="Type your test case description here...", key="query_input")
    submit_button = st.form_submit_button("Generate Test Case", type="primary", use_container_width=True)
    
    if submit_button and query and query.strip():
        init_session_state()
        
        with st.spinner("Generating test case..."):
            try:
                result = asyncio.run(call_tool("get_test_case", query))
                
                if result:
                    try:
                        if isinstance(result, str):
                            if result.strip().startswith('[') and result.strip().endswith(']'):
                                parsed_result = json.loads(result)
                            elif result.strip().startswith('{') and result.strip().endswith('}'):
                                parsed_result = json.loads(result)
                            else:
                                import re
                                json_match = re.search(r'(\[.*\]|\{.*\})', result, re.DOTALL)
                                if json_match:
                                    parsed_result = json.loads(json_match.group())
                                else:
                                    raise json.JSONDecodeError("No JSON found", result, 0)
                        else:
                            parsed_result = result
                        
                        if isinstance(parsed_result, list):
                            st.session_state.test_cases_list = parsed_result
                            st.session_state.current_test_case_index = 0
                            st.session_state.test_case_data = parsed_result[0] if parsed_result else None
                            st.success(f"Generated {len(parsed_result)} test cases!")
                        else:
                            st.session_state.test_cases_list = [parsed_result]
                            st.session_state.current_test_case_index = 0
                            st.session_state.test_case_data = parsed_result
                            st.success("Test case generated successfully!")
                        
                    except json.JSONDecodeError as e:
                        st.warning(f"Could not parse JSON: {str(e)}")
                        st.write("Raw response:")
                        st.text_area("Raw Result", value=str(result), height=200, key="raw_error_result")
                        st.session_state.test_case_data = {"raw_result": result}
                else:
                    st.error("No result returned from the tool")
                    
            except Exception as e:
                st.error(f"Error generating test case: {str(e)}")
                st.write("Error details:")
                import traceback
                st.code(traceback.format_exc())
                
                if 'test_case_data' in st.session_state:
                    del st.session_state.test_case_data

init_session_state()

if st.session_state.test_cases_list and len(st.session_state.test_cases_list) > 0:
    if st.session_state.test_case_data:
        st.subheader(f"Test Case {st.session_state.current_test_case_index + 1}")
        
        data = st.session_state.test_case_data
    
    name = data.get("Name", data.get("name", ""))
    if name:
        st.session_state.editable_fields['name'] = st.text_area(
            "Name:", 
            value=name, 
            height=50,
            key="name_field"
        )
    
    precondition = data.get("Precondition", data.get("precondition", ""))
    if precondition:
        st.session_state.editable_fields['precondition'] = st.text_area(
            "Precondition:", 
            value=precondition, 
            height=100,
            key="precondition_field"
        )
    
    steps = data.get("Steps", data.get("steps", data.get("Step", "")))
    if steps:
        if isinstance(steps, list):
            steps_text = "\n".join([f"{i+1}. {step}" for i, step in enumerate(steps)])
        else:
            steps_text = str(steps)
        
        st.session_state.editable_fields['steps'] = st.text_area(
            "Steps:", 
            value=steps_text, 
            height=150,
            key="steps_field"
        )
    
    expected_result = data.get("Expected result", data.get("expected_result", ""))
    if expected_result:
        st.session_state.editable_fields['expected_result'] = st.text_area(
            "Expected Result:", 
            value=expected_result, 
            height=100,
            key="expected_result_field"
        )
    
    fields = data.get("Fields", data.get("fields", ""))
    if fields:
        st.session_state.editable_fields['fields'] = st.text_area(
            "Fields:", 
            value=fields, 
            height=100,
            key="fields_field"
        )
    
    if "raw_result" in data:
        st.text_area("Raw Result:", value=data["raw_result"], height=200, key="raw_result_field")
    
    st.divider()
    
    col1, col2, col3, col4 = st.columns([1, 1, 2, 1])
    
    with col1:
        if st.button("Previous", disabled=st.session_state.current_test_case_index == 0):
            if st.session_state.current_test_case_index > 0:
                st.session_state.current_test_case_index -= 1
                st.session_state.test_case_data = st.session_state.test_cases_list[st.session_state.current_test_case_index]
                st.rerun()
    
    with col2:
        if st.button("Next", disabled=st.session_state.current_test_case_index >= len(st.session_state.test_cases_list) - 1):
            if st.session_state.current_test_case_index < len(st.session_state.test_cases_list) - 1:
                st.session_state.current_test_case_index += 1
                st.session_state.test_case_data = st.session_state.test_cases_list[st.session_state.current_test_case_index]
                st.rerun()
    
    with col3:
        st.write(f"Test Case {st.session_state.current_test_case_index + 1} of {len(st.session_state.test_cases_list)}")
    
    with col4:
        if st.button("Remove Current"):
            st.session_state.test_cases_list.pop(st.session_state.current_test_case_index)
            if st.session_state.current_test_case_index >= len(st.session_state.test_cases_list):
                st.session_state.current_test_case_index = max(0, len(st.session_state.test_cases_list) - 1)
            
            if st.session_state.test_cases_list:
                st.session_state.test_case_data = st.session_state.test_cases_list[st.session_state.current_test_case_index]
            else:
                st.session_state.test_case_data = None
            st.rerun()
    
    st.divider()
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("Add Test Case", type="primary"):
            current_test_case = st.session_state.test_cases_list[st.session_state.current_test_case_index]
            st.success(f"Test case {st.session_state.current_test_case_index + 1} added successfully!")
    
    with col2:
        if st.button("Clear All"):
            st.session_state.test_cases_list = []
            st.session_state.current_test_case_index = 0
            st.session_state.test_case_data = None
            st.session_state.editable_fields = {}
            st.rerun()