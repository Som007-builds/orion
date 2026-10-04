"""Offline investigation-note monoculture auditor."""
from __future__ import annotations
from collections import defaultdict
from collections.abc import Iterable, Mapping
import hashlib, re
from dataclasses import dataclass

@dataclass(frozen=True)
class MonocultureResult:
    entity_id: str
    monoculture_index: float
    high_critical_cluster_share: float
    cluster_id: str | None
    evidence_row_ids: tuple[str, ...]
    analyst_ids: tuple[str, ...] = ()

def _shingles(text: str, n: int = 3) -> set[str]:
    words=re.findall(r"[a-z0-9]+", text.lower()); return {" ".join(words[i:i+n]) for i in range(max(0,len(words)-n+1))}

def audit_notes(notes: Iterable[Mapping[str, object]], *, threshold: float = .82, num_perm: int = 32) -> list[MonocultureResult]:
    """Generate MinHash-like deterministic buckets then exact TF-IDF clusters.

    Hash buckets are candidate generation only; similarity is calculated by
    TF-IDF, avoiding quadratic comparisons across unrelated notes.
    """
    rows=list(notes)
    if not rows: return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:  # offline minimal environment; deterministic TF-IDF equivalent
        from collections import Counter
        import math
        def cosine_similarity(matrix):
            def sim(a,b):
                keys=set(a)|set(b); dot=sum(a.get(k,0)*b.get(k,0) for k in keys)
                return dot/(math.sqrt(sum(v*v for v in a.values()))*math.sqrt(sum(v*v for v in b.values())) or 1)
            return [[sim(a,b) for b in matrix] for a in matrix]
        df=Counter(w for t in rows for w in set(re.findall(r"[a-z0-9]+",str(t.get("text","" )).lower())))
        class TfidfVectorizer:
            def fit_transform(self, texts):
                return [{w: (txt.count(w)*math.log((1+len(texts))/(1+df[w]))) for w in set(re.findall(r"[a-z0-9]+",txt.lower()))} for txt in texts]
    buckets=defaultdict(list)
    for i,r in enumerate(rows):
        text=str(r.get("text", "")); sh=_shingles(text)
        if not sh: continue
        sig=[]
        for seed in range(num_perm):
            sig.append(min(int(hashlib.sha256(f"{seed}:{x}".encode()).hexdigest(),16) for x in sh))
        for j in range(0,num_perm,4): buckets[(str(r.get("entity_id","")), tuple(sig[j:j+4]))].append(i)
    seen=set(); out=[]
    for inds in buckets.values():
        inds=sorted(set(inds))
        if len(inds)<2 or any(i in seen for i in inds): continue
        texts=[str(rows[i].get("text","")) for i in inds]
        sim=cosine_similarity(TfidfVectorizer().fit_transform(texts)); cluster=[inds[0]]+[i for j,i in enumerate(inds[1:],1) if (sim[0,j] if hasattr(sim, "shape") else sim[0][j])>=threshold]
        if len(cluster)<2: continue
        # MSSP/shared-template evidence is a documented benign explanation,
        # not a monoculture finding. The flag is supplied by normalized
        # metadata, never inferred from detector output.
        if all(bool(rows[i].get("legitimate_template", False)) for i in cluster):
            continue
        seen.update(cluster); high=sum(str(rows[i].get("severity","" )).upper() in {"HIGH","CRITICAL"} for i in cluster)
        eid=str(rows[cluster[0]].get("entity_id","")); concentration=len(cluster)/max(1,sum(1 for r in rows if str(r.get("entity_id",""))==eid))
        if concentration < 0.5: continue
        cid=hashlib.sha256("|".join(str(rows[i].get("row_id",i)) for i in cluster).encode()).hexdigest()[:12]
        out.append(MonocultureResult(eid, concentration, high/len(cluster), cid, tuple(str(rows[i].get("row_id",i)) for i in cluster), tuple(sorted({str(rows[i].get("analyst_id","")) for i in cluster}))))
    return sorted(out,key=lambda x:(x.entity_id,x.cluster_id or ""))

def monoculture_index(notes: Iterable[Mapping[str, object]]) -> list[MonocultureResult]:
    return audit_notes(notes)
