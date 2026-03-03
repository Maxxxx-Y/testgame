from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

@dataclass
class CardDef:
    id: str
    name: str
    axis: str
    cost: int
    targeting: str
    desc: str
    effects: List[Dict[str, Any]]

def load_cards(path: str) -> List[CardDef]:
    import json
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    cards: List[CardDef] = []
    for c in raw:
        cards.append(CardDef(
            id=c["id"], name=c["name"], axis=c["axis"], cost=int(c["cost"]),
            targeting=c["targeting"], desc=c.get("desc",""),
            effects=list(c.get("effects",[]))
        ))
    return cards
