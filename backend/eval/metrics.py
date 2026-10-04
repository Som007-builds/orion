"""Small dependency-free ranking metrics."""
from __future__ import annotations
import math
def precision_recall(scores, positives, k=None):
    best={}
    for entity,score in scores: best[entity]=max(float(score),best.get(entity,float("-inf")))
    order=sorted(best.items(),key=lambda x:(-x[1],x[0])); chosen=order[:k] if k else order; p=set(positives); hit=sum(x[0] in p for x in chosen); return {"precision":hit/max(1,len(chosen)),"recall":hit/max(1,len(p)),"f1":2*hit/max(1,len(chosen)+len(p))}
def ndcg(scores, positives):
    order=sorted(scores,key=lambda x:-x[1]); p=set(positives); dcg=sum((1/math.log2(i+2)) for i,x in enumerate(order) if x[0] in p); ideal=sum(1/math.log2(i+2) for i in range(min(len(p),len(order)))); return dcg/ideal if ideal else 0.
