import numpy as np
import matplotlib.pyplot as plt
from sklearn.manifold import TSNE
import plotly.express as px
from sklearn.decomposition import PCA
from umap import UMAP
import json
import re
from vector import Vector

vectorstore = Vector().get_db()
results = vectorstore.get(include=["embeddings", "metadatas", "documents"])

embeddings = np.array(results["embeddings"])
documents = results["documents"]
metadatas = results["metadatas"]

reduced = PCA(n_components=20).fit_transform(embeddings)
embeddings_2d = TSNE(n_components=2).fit_transform(reduced)

def extract_epic_from_text(text):
    match = re.search(r"Epic:\s*([^;]+)", text)
    if match:
        return match.group(1).strip()
    return "Unknown"


epic_labels = [extract_epic_from_text(doc) for doc in documents]

hover_texts = [
    f"Epic: {epic_labels[i]}<br>Doc: {documents[i][:150]}..." if len(documents[i]) > 150
    else f"Epic: {epic_labels[i]}<br>Doc: {documents[i]}"
    for i in range(len(documents))
]

fig = px.scatter(
    x=embeddings_2d[:, 0],
    y=embeddings_2d[:, 1],
    color=epic_labels,
    hover_data={"text": hover_texts},
    title="Chroma Embeddings",
    labels={"x": "D 1", "y": "D 2"}
)

fig.update_traces(marker=dict(size=8, opacity=0.8))
fig.show()