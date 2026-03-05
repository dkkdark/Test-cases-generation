import asyncio
import streamlit as st
import json
import re
import requests
from config import Config
import traceback

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

async def call_tool(tool_name: str, args: dict | None = None, query_text: str = ""):
    try:
        base = f"http://{Config.Server.HOST}:{Config.Server.PORT}"
        if tool_name == "get_test_case":
            resp = requests.post(f"{base}/get_test_case", json={"query": query_text})
            resp.raise_for_status()
            return (resp.json() or {}).get("result")
        elif tool_name == "load_test_cases_from_allure":
            resp = requests.post(f"{base}/load_test_cases_from_allure", json={})
            resp.raise_for_status()
            return (resp.json() or {}).get("result")
        elif tool_name == "save_allure_test_cases":
            resp = requests.post(f"{base}/save_allure_test_cases", json={})
            resp.raise_for_status()
            return (resp.json() or {}).get("result")
        elif tool_name == "create_test_case":
            args = args or {}
            resp = requests.post(f"{base}/create_test_case", json=args)
            resp.raise_for_status()
            return (resp.json() or {}).get("result")
    except Exception as e:
        print(f"call_tool error for {tool_name}: {e}")
        print(traceback.format_exc())
        raise e

with st.sidebar:
    st.header("Additional Actions")
    
    if st.button("Load Allure test cases"):
        with st.spinner("Loading..."):
            try:
                res = asyncio.run(call_tool("load_test_cases_from_allure"))
                st.write(res)
            except Exception as e:
                st.error(f"Load Allure test cases failed: {str(e)}")
                st.code(traceback.format_exc())
    
    if st.button("Save Allure test cases to DB"):
        with st.spinner("Saving..."):
            try:
                res = asyncio.run(call_tool("save_allure_test_cases"))
                st.write(res)
            except Exception as e:
                st.error(f"Save Allure test cases failed: {str(e)}")
                st.code(traceback.format_exc())

with st.form("test_case_form"):
    query = st.text_input("Enter your query to create a test case:", placeholder="Type your test case description here...", key="query_input")
    submit_button = st.form_submit_button("Generate Test Case", type="primary", use_container_width=True)
    
    if submit_button and query and query.strip():
        init_session_state()
        
        with st.spinner("Generating test case..."):
            try:
                result = asyncio.run(call_tool("get_test_case", query_text=query))
                
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
    
    col_left, col_right = st.columns([2, 1])

    with col_left:
        name = data.get("Name", data.get("name", ""))
        if name:
            st.session_state.editable_fields['name'] = st.text_area(
                "Name:", 
                value=name, 
                height=68,
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

    with col_right:
        fields = data.get("Fields", data.get("fields", ""))
        try:
            parsed_fields = []
            if isinstance(fields, list):
                for item in fields:
                    if isinstance(item, dict):
                        fname = item.get("fieldName")
                        fvalue = item.get("fieldValue")
                        if fname and fvalue is not None:
                            parsed_fields.append({"fieldName": fname, "fieldValue": fvalue})
            elif isinstance(fields, str) and fields:
                pairs = [p.strip() for p in fields.split(";") if p.strip()]
                for pair in pairs:
                    if ":" in pair:
                        fname, fvalue = pair.split(":", 1)
                        fname = fname.strip()
                        fvalue = fvalue.strip()
                        if fname:
                            parsed_fields.append({"fieldName": fname, "fieldValue": fvalue})

            if parsed_fields:
                st.write("Fields")
                structured_values = {}
                for idx, f in enumerate(parsed_fields):
                    label = f.get("fieldName") or f"Field {idx+1}"
                    default_value = f.get("fieldValue", "")
                    input_value = st.text_input(label, value=default_value, key=f"field_input_{idx}")
                    structured_values[label] = input_value
                st.session_state.editable_fields['fields_structured'] = structured_values
        except Exception:
            pass
    
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
            name_val = st.session_state.editable_fields.get('name') or \
                current_test_case.get("Name", current_test_case.get("name", "")).strip()

            precondition_val = st.session_state.editable_fields.get('precondition') or \
                current_test_case.get("Precondition", current_test_case.get("precondition", "")).strip()

            steps_text_val = st.session_state.editable_fields.get('steps')
            if steps_text_val is None:
                src_steps = current_test_case.get("Steps", current_test_case.get("steps", current_test_case.get("Step", [])))
                if isinstance(src_steps, list):
                    steps_list_val = [str(s).strip() for s in src_steps if str(s).strip()]
                else:
                    steps_text_val = str(src_steps)
                    steps_list_val = [s for s in [re.sub(r"^\s*\d+\.\s*", "", line).strip() for line in steps_text_val.splitlines()] if s]
            else:
                steps_list_val = [s for s in [re.sub(r"^\s*\d+\.\s*", "", line).strip() for line in steps_text_val.splitlines()] if s]

            expected_val = st.session_state.editable_fields.get('expected_result') or \
                current_test_case.get("Expected result", current_test_case.get("expected_result", "")).strip()

            structured = st.session_state.editable_fields.get('fields_structured', {})
            fields_list = [{"fieldName": k, "fieldValue": v} for k, v in structured.items() if str(k).strip() and k != "Product"]
            if not fields_list:
                raw_fields = current_test_case.get("Fields", current_test_case.get("fields", []))
                if isinstance(raw_fields, dict):
                    fields_list = [{"fieldName": k, "fieldValue": v} for k, v in raw_fields.items() if k != "Product"]
                elif isinstance(raw_fields, list):
                    fields_list = [
                        {"fieldName": f.get("fieldName") or f.get("name"), "fieldValue": f.get("fieldValue") or f.get("value")}
                        for f in raw_fields if isinstance(f, dict) and (f.get("fieldName") or f.get("name")) != "Product"
                    ]

            args = {
                "name": name_val,
                "precondition": precondition_val,
                "steps": steps_list_val,
                "expected_result": expected_val,
                "fields": fields_list,
            }

            print(args)

            with st.spinner("Creating test case in Allure..."):
                try:
                    res = asyncio.run(call_tool("create_test_case", args=args))
                    st.success(res or "Test case was successfully created!")
                except Exception as e:
                    st.error(f"Create test case failed: {str(e)}")
                    st.code(traceback.format_exc())
    
    with col2:
        if st.button("Clear All"):
            st.session_state.test_cases_list = []
            st.session_state.current_test_case_index = 0
            st.session_state.test_case_data = None
            st.session_state.editable_fields = {}
            st.rerun()