#!/usr/bin/env python3
"""
生成统一本体：合并 AOF 语义本体(genshin_ontology.owl) + 数据层本体(hk4e.ttl)。
产出 hk4e-unified.ttl，含：
- 语义层类（PlayableCharacter/Region/GameElement...）
- 数据层类（Avatar/Item/Dungeon...）及其 subClassOf 域
- 桥接关系（PlayableCharacter ↔ Avatar 等）
"""
from pathlib import Path
from rdflib import Graph, RDF, RDFS, OWL, Namespace, Literal, URIRef

BASE = Path("/Users/chaihao/.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta/aof")
SEM = Namespace("http://genshin.mihoyo.com/ontology#")
DAT = Namespace("http://hk4e.mihoyo.com/ontology#")
BRIDGE = Namespace("http://hk4e.mihoyo.com/bridge#")

# 1. 加载两个源本体
g = Graph()
g.bind("genshin", SEM)
g.bind("hk4e", DAT)
g.bind("bridge", BRIDGE)
g.bind("owl", OWL)
g.bind("rdfs", RDFS)

g.parse("/Users/chaihao/LLM/AOF/ontologies/genshin_ontology.owl")
g.parse(BASE / "hk4e.ttl", format="turtle")

# 2. 顶层桥接：数据类 ↔ 语义类
# 语义(游戏内容) ← 数据(表实体)
bridge_map = {
    "Avatar": "PlayableCharacter",   # 角色
    "Item": "GameElement",           # 道具→游戏元素
    "Reliquary": "GameElement",
    "Weapon": "GameElement",
    "Area": "Region",                # 区域→国度
    "Scene": "Region",
    "Dungeon": "GameplaySystem",     # 副本→玩法系统
    "Gacha": "GameplaySystem",       # 卡池→玩法系统
    "Achievement": "GameplaySystem",
}

# 声明桥接 owl:Class + rdfs:seeAlso
u = Graph()
u.bind("genshin", SEM); u.bind("hk4e", DAT); u.bind("bridge", BRIDGE)
u.bind("owl", OWL); u.bind("rdfs", RDFS)

# ontology 头
u.add((URIRef("http://hk4e.mihoyo.com/ontology"), RDF.type, OWL.Ontology))
u.add((URIRef("http://hk4e.mihoyo.com/ontology"), RDFS.label,
       Literal("Genshin Impact Unified Ontology (Semantic + Data)", lang="en")))
u.add((URIRef("http://hk4e.mihoyo.com/ontology"), RDFS.comment,
       Literal("合并语义层(genshin_ontology.owl)与数据层(hk4e.ttl)", lang="zh")))

# 拷贝两边所有三元组
for t in g:
    u.add(t)

# 加桥接三元组
for dcls, scls in bridge_map.items():
    d = DAT[dcls]; s = SEM[scls]
    # 数据类 rdfs:seeAlso 语义类
    u.add((d, RDFS.seeAlso, s))
    u.add((s, RDFS.seeAlso, d))
    # 记录桥接谓词
    u.add((d, BRIDGE.semanticCounterpart, s))

# 3. 派生一些语义级 ObjectProperty，让数据实体能连到语义概念
u.add((BRIDGE.wieldsElement, RDF.type, OWL.ObjectProperty))
u.add((BRIDGE.wieldsElement, RDFS.label, Literal("使用元素", lang="zh")))
u.add((BRIDGE.wieldsElement, RDFS.domain, DAT.Avatar))
u.add((BRIDGE.wieldsElement, RDFS.range, SEM.GameElement))

u.add((BRIDGE.belongsToRegion, RDF.type, OWL.ObjectProperty))
u.add((BRIDGE.belongsToRegion, RDFS.label, Literal("属于国度", lang="zh")))
u.add((BRIDGE.belongsToRegion, RDFS.domain, DAT.Avatar))
u.add((BRIDGE.belongsToRegion, RDFS.range, SEM.Region))

u.add((BRIDGE.isMemberOf, RDF.type, OWL.ObjectProperty))
u.add((BRIDGE.isMemberOf, RDFS.label, Literal("属于组织", lang="zh")))
u.add((BRIDGE.isMemberOf, RDFS.domain, DAT.Avatar))
u.add((BRIDGE.isMemberOf, RDFS.range, SEM.LoreFaction))

out = BASE / "hk4e-unified.ttl"
u.serialize(destination=str(out), format="turtle")

# 统计
print(f"统一本体: {out}")
print(f"  三元组: {len(u)}")
print(f"  owl:Class: {len(set(u.subjects(RDF.type, OWL.Class)))}")
print(f"  ObjectProperty: {len(set(u.subjects(RDF.type, OWL.ObjectProperty)))}")
print(f"  DatatypeProperty: {len(set(u.subjects(RDF.type, OWL.DatatypeProperty)))}")
print(f"  桥接关系: {len(bridge_map)}")

# 校验
from rdflib import Graph as G2
g2 = G2(); g2.parse(str(out), format="turtle")
print(f"  ✓ 重新解析成功: {len(g2)} 三元组")
