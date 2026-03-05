from langchain_openai import OpenAIEmbeddings
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from dotenv import load_dotenv
import json
import os

load_dotenv(override=True)

class Vector:
    def __init__(self):
        self.persist_dir = "chroma_testcase"
        provider = os.getenv("EMBEDDINGS_PROVIDER", "openai").lower()
        if provider == "ollama":
            self.embeddings = OllamaEmbeddings(
                model=os.getenv("EMBEDDING_MODEL"),
                base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            )
        else:
            self.embeddings = OpenAIEmbeddings(
                api_key=os.getenv("OPENAI_API")
            )

    def get_db(self):
        vectorstore = Chroma(
            persist_directory=self.persist_dir,
            embedding_function=self.embeddings
        )
        return vectorstore

    def add_testcases(self):
        vectorstore = Chroma(persist_directory=self.persist_dir, embedding_function=self.embeddings)
        vectorstore.reset_collection() 

        with open("testcases.json", "r", encoding="utf-8") as f:
            all_testcases = json.load(f)

            for tc in all_testcases:
                text = self._to_text(tc)
                vectorstore.add_texts(
                    texts=[text],
                    ids=[str(tc["id"])]
                )

        return vectorstore


    def _to_text(self, tc: dict) -> str:
        fields = tc.get('fields', [])
        
        field_dict = {}
        if isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict):
                    field_name = item.get('fieldName', '')
                    field_value = item.get('fieldValue', '')
                    if field_name and field_value:
                        field_dict[field_name] = field_value
            formatted_fields = '; '.join(
                f"{name}: {value}" for name, value in field_dict.items()
            )
        else:
            formatted_fields = str(fields)
            field_dict = fields if isinstance(fields, dict) else {}

        product = field_dict.get('Product', '')
        epic = field_dict.get('Epic', '')
        feature = field_dict.get('Feature', '')
        component = field_dict.get('Component', '')
        
        signature_parts = []
        if product:
            signature_parts.append(f"Product: {product}")
        if epic:
            signature_parts.append(f"Epic: {epic}")
        if feature:
            signature_parts.append(f"Feature: {feature}")
        if component:
            signature_parts.append(f"Component: {component}")
        
        signature = " | ".join(signature_parts) if signature_parts else ""

        return f"""
        {signature}
        Fields: {formatted_fields}
        Name: {tc['name']}
        Precondition: {tc['precondition']}
        Steps: {'; '.join(tc['steps'])}
        Expected result: {tc['expectedResult']}
        """
