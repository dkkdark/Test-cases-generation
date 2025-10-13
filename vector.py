from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from dotenv import load_dotenv
import json
import os

load_dotenv()

class Vector:
    def __init__(self):
        self.persist_dir = "chroma_testcase"
        self.embeddings = OpenAIEmbeddings(api_key=os.getenv("OPENAI_API"))

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
            print(all_testcases)

            for tc in all_testcases:
                text = self._to_text(tc)
                vectorstore.add_texts(
                    texts=[text],
                    ids=[str(tc["id"])]
                )

        return vectorstore


    def _to_text(self, tc: dict) -> str:
        return f"""
        Name: {tc['name']}
        Precondition: {tc['precondition']}
        Steps: {'; '.join(tc['steps'])}
        Expected result: {tc['expectedResult']}
        Fields: {'; '.join(tc['fields'])}
        """
