"""Verify new acts are retrievable from Qdrant using the SAME resolution the retriever uses."""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from qdrant_client import QdrantClient, models
from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType
from app.config import settings
from app.services.legal_normalizer import resolve_act_robust, normalize_act

TextEmbedding.add_custom_model(
    model="intfloat/multilingual-e5-small",
    pooling=PoolingType.MEAN,
    normalization=True,
    sources=ModelSource(hf="intfloat/multilingual-e5-small"),
    dim=384,
    model_file="onnx/model.onnx",
)

COLL = "bd_laws_e5_small"

PROBES = [
    ("copyright act", "কপিরাইট আইন, ২০২৩", 128),
    ("labour act", "বাংলাদেশ শ্রম আইন, ২০০৬", 369),
    ("cyber security act", "সাইবার সুরক্ষা আইন, ২০২৬", 52),
    ("data protection act", "ব্যক্তিগত উপাত্ত সুরক্ষা আইন, ২০২৬", 45),
    ("ict act", "তথ্য ও যোগাযোগ প্রযুক্তি আইন, ২০০৬", 90),
    ("telecom act", "বাংলাদেশ টেলিযোগাযোগ আইন, ২০০১", 115),
]

SEMANTIC = [
    ("who is the first owner of copyright in a commissioned software work", "কপিরাইট আইন, ২০২৩", "15"),
    ("termination of employment by employer notice period", "বাংলাদেশ শ্রম আইন, ২০০৬", "26"),
    ("data breach notification obligation service provider", "সাইবার সুরক্ষা আইন, ২০২৬", None),
    ("consent required for processing personal data rights", "ব্যক্তিগত উপাত্ত সুরক্ষা আইন, ২০২৬", None),
]


def main():
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY, timeout=120.0)
    print("collection count:", client.count(COLL, exact=True).count)

    print("\n--- 1) act filter via resolve_act_robust (retriever's own path) ---")
    ok = True
    for probe, expect_canon, expect_docs in PROBES:
        resolved = resolve_act_robust(probe)
        n = 0
        if resolved:
            n = client.count(
                COLL, exact=True,
                count_filter=models.Filter(must=[
                    models.FieldCondition(key="metadata.act_norm", match=models.MatchValue(value=resolved))
                ]),
            ).count
        status = "OK " if resolved == expect_canon and n == expect_docs else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"  [{status}] {probe!r:22} -> {resolved!r:48} docs={n}/{expect_docs}")

    print("\n--- 2) semantic search (EN query, act-filtered) ---")
    model = TextEmbedding(model_name="intfloat/multilingual-e5-small")
    for query, act, expect_sec in SEMANTIC:
        resolved = resolve_act_robust(act)
        vec = list(model.embed([f"query: {query}"]))[0].tolist()
        res = client.query_points(
            COLL, query=vec, limit=3,
            query_filter=models.Filter(must=[
                models.FieldCondition(key="metadata.act_norm", match=models.MatchValue(value=resolved))
            ]),
        ).points
        if res:
            top = res[0]
            meta = top.payload.get("metadata", {})
            sec = meta.get("section_norm", "")
            flag = ""
            if expect_sec:
                flag = "OK " if any(p.payload.get("metadata", {}).get("section_norm") == expect_sec for p in res) else "?? "
            print(f"  [{flag}] {query[:48]:50} -> {meta.get('act','')} S.{meta.get('section_norm')} score={top.score:.3f}")
        else:
            ok = False
            print(f"  [FAIL] {query[:48]:50} -> no hits under {resolved}")

    print("\nVERDICT:", "ALL GOOD" if ok else "ISSUES FOUND")


if __name__ == "__main__":
    main()
