from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import os, json, time

from .model import Cell, Unit, TurnModifiers, GameEventLog
from .util import DIRS, DIR_ORDER, clamp, DeterministicRNG
from .cards import load_cards, CardDef
from . import physics
from . import ai
from .ui import UI

@dataclass
class GameState:
    w: int = 8
    h: int = 8
    grid: List[List[Cell]] = None
    units: Dict[str, Unit] = None
    units_max_hp: Dict[str, int] = None
    ap: int = 3
    turn: int = 1
    stage_idx: int = 0
    stage_name: str = ""
    modifiers: TurnModifiers = None
    log: GameEventLog = None
    rng: DeterministicRNG = None

    # deck system
    deck: List[CardDef] = None
    discard: List[CardDef] = None
    hand: List[CardDef] = None

    disp_queue: List[Dict[str, Any]] = None

    # replay
    snapshot_base: Any = None  # for replay: base snapshot at turn start
    snapshot_current: Any = None

    # boss
    boss_rule_pool: List[Dict[str,Any]] = None
    boss_every3: List[Dict[str,Any]] = None
    boss_uid: Optional[str] = None

    def in_bounds(self, x,y) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def cell(self, x,y) -> Cell:
        return self.grid[y][x]

    def format_log_lines(self) -> List[str]:
        # render last ~N events into compact lines
        out = []
        for i,ev in enumerate(self.log.events):
            k = ev.kind
            d = ev.data
            if k == "temp_cell":
                out.append(f"[T] ({d['x']},{d['y']}) {d['before']:.1f}->{d['after']:.1f}")
            elif k == "status_apply":
                out.append(f"[S] {d['uid']} +{d['status']}({d['ttl']})")
            elif k == "evaporation":
                out.append(f"[T] 蒸发 ({d['x']},{d['y']}) steps={d['steps']}")
            elif k == "discharge":
                out.append(f"[Q] {d['from_']}->{d['to']} Δ={d['delta']} dmg={d['dmg']}")
            elif k == "displace_move":
                out.append(f"[D] {d['uid']} {d['from_']}->{d['to']} {d['dir']}")
            elif k == "displace_wall":
                out.append(f"[D] {d['uid']} 撞墙 dmg={d['dmg']}")
            elif k == "displace_hit_unit":
                out.append(f"[D] 碰撞 {d['a']}×{d['b']} dmg={d['dmg']}")
            elif k == "height_damage":
                out.append(f"[D] 落差 {d['uid']} dmg={d['dmg']}")
            elif k == "unit_damage":
                out.append(f"[HP] {d['uid']} -{d['amount']} ({d['reason']})")
            elif k == "unit_die":
                out.append(f"[HP] {d['uid']} 死亡")
            elif k == "card_play":
                out.append(f"[CARD] {d['card_id']} {d.get('target','')}")
            elif k == "turn_start":
                out.append(f"=== Turn {d['turn']} ===")
            elif k == "turn_end":
                out.append(f"=== End Turn {d['turn']} ===")
            elif k == "stage_start":
                out.append(f"=== {d['name']} ===")
            elif k == "reward_card":
                out.append(f"[REWARD] 获得卡：{d['card_id']}")
            elif k == "boss_rule":
                out.append(f"[BOSS] {d['desc']}")
            elif k == "global_charge_pulse":
                out.append(f"[BOSS] 全场电荷 +{d['amount']}")
            elif k == "trap_trigger":
                out.append(f"[TRAP] ({d['x']},{d['y']}) 触发 +Q")
            elif k == "capacitor_release":
                out.append(f"[CAP] ({d['x']},{d['y']}) 释放 {d['amount']}")
            else:
                # fallback compact
                out.append(f"{k}: {d}")
        return out

    def turn_order_enemies(self):
        for uid,u in self.units.items():
            if u.team == "enemy" and u.hp > 0:
                yield uid
        if self.boss_uid and self.boss_uid in self.units and self.units[self.boss_uid].hp > 0:
            yield self.boss_uid

    def damage_unit(self, uid: str, amount: int, log, reason: str):
        u = self.units.get(uid)
        if not u or u.hp <= 0:
            return
        amount = max(0, int(amount))
        if amount == 0:
            return
        u.hp -= amount
        log.push("unit_damage", uid=uid, amount=amount, reason=reason)
        if u.hp <= 0:
            u.hp = 0
            # remove from grid
            self.grid[u.y][u.x].unit_id = None
            log.push("unit_die", uid=uid)

    def move_unit(self, uid: str, dir_name: str, steps: int, log, via: str, no_damage: bool=False):
        # used for AI move (no damage)
        if uid not in self.units or self.units[uid].hp <= 0:
            return
        u = self.units[uid]
        dx,dy = DIRS[dir_name]
        for _ in range(steps):
            nx,ny = u.x+dx, u.y+dy
            if not self.in_bounds(nx,ny):
                break
            if self.grid[ny][nx].unit_id is not None:
                break
            self.grid[u.y][u.x].unit_id = None
            self.grid[ny][nx].unit_id = uid
            u.x,u.y = nx,ny
            log.push("unit_move", uid=uid, to=(nx,ny), via=via)

    def enqueue_displacement(self, uid: str, dir_name: str, steps: int, log, source: str, collision_damage_override: Optional[int]=None):
        ev = {"uid": uid, "dir": dir_name, "steps": steps, "source": source}
        if collision_damage_override is not None:
            ev["collision_damage_override"] = collision_damage_override
        self.disp_queue.append(ev)
        log.push("displace_enqueue", **ev)

    def enqueue_shockwave_center(self, cx: int, cy: int, steps: int, log, source: str):
        # push adjacent units away from center
        for d in DIR_ORDER:
            dx,dy = DIRS[d]
            nx,ny = cx+dx, cy+dy
            if not self.in_bounds(nx,ny): 
                continue
            uid = self.grid[ny][nx].unit_id
            if uid and uid in self.units and self.units[uid].hp > 0:
                # away direction is same as neighbor direction from center
                self.enqueue_displacement(uid, d, steps, log, source=source)

    def enqueue_gravity_pull(self, cx: int, cy: int, log, source: str):
        # pull adjacent units toward center (reverse dir)
        rev = {"up":"down","down":"up","left":"right","right":"left"}
        for d in DIR_ORDER:
            dx,dy = DIRS[d]
            nx,ny = cx+dx, cy+dy
            if not self.in_bounds(nx,ny): 
                continue
            uid = self.grid[ny][nx].unit_id
            if uid and uid in self.units and self.units[uid].hp > 0:
                self.enqueue_displacement(uid, rev[d], 1, log, source=source)

def load_json(path):
    with open(path,"r",encoding="utf-8") as f:
        return json.load(f)

def make_empty_grid(w,h):
    return [[Cell() for _ in range(w)] for _ in range(h)]

def init_stage(state: GameState, encounter: Dict[str,Any], units_db: Dict[str,Any]):
    state.grid = make_empty_grid(state.w, state.h)
    state.units = {}
    state.units_max_hp = {}
    state.disp_queue = []
    state.ap = 3
    state.turn = 1
    state.stage_name = encounter["name"]

    # base heights: 0, but allow simple perlin-ish variation by seed (optional) -> keep deterministic & minimal
    # We'll keep flat to avoid introducing new mechanics.

    # apply terrain overrides
    for ov in encounter.get("terrain_overrides",[]):
        x,y = ov["x"], ov["y"]
        state.grid[y][x].terrain = ov["terrain"]

    # spawn player at (1,3)
    pconf = units_db["player"]
    p = Unit(uid="P1", team="player", x=1, y=3, hp=pconf["hp"], move=pconf["move"],
             conductivity=pconf.get("conductivity",1.0), weight=pconf.get("weight","medium"),
             tags=list(pconf.get("tags",[])))
    state.units[p.uid] = p
    state.units_max_hp[p.uid] = pconf["hp"]
    state.grid[p.y][p.x].unit_id = p.uid

    # enemies
    for i,e in enumerate(encounter.get("enemies",[])):
        et = e["type"]
        conf = units_db[et]
        uid = f"E{i+1}"
        u = Unit(uid=uid, team="enemy", x=e["x"], y=e["y"], hp=conf["hp"], move=conf["move"],
                 conductivity=conf.get("conductivity",1.0), weight=conf.get("weight","medium"),
                 tags=list(conf.get("tags",[])),
                 water_regen=int(conf.get("water_regen",0)),
                 shock_taken_mult=float(conf.get("shock_taken_mult",1.0)))
        state.units[uid] = u
        state.units_max_hp[uid] = conf["hp"]
        state.grid[u.y][u.x].unit_id = uid

    # boss
    state.boss_uid = None
    state.boss_rule_pool = None
    state.boss_every3 = None
    if "boss" in encounter:
        b = encounter["boss"]
        uid = "BOSS"
        u = Unit(uid=uid, team="boss", x=6, y=3, hp=int(b["hp"]), move=0, conductivity=1.0, weight="high", tags=[])
        state.units[uid] = u
        state.units_max_hp[uid] = int(b["hp"])
        state.grid[u.y][u.x].unit_id = uid
        state.boss_uid = uid
        state.boss_rule_pool = list(b.get("rule_modifiers",[]))
        state.boss_every3 = list(b.get("every_3_turns",[]))

def init_deck(all_cards: List[CardDef], rng: DeterministicRNG) -> Tuple[List[CardDef], List[CardDef], List[CardDef]]:
    # starter: 10 cards (温度/电荷/位移各 3 + 1 push)
    ids = {"T_HEAT_40","T_COOL_40","T_MELT_METAL",
           "C_ADD_3","C_SUB_3","C_SWAP",
           "D_PUSH_1","D_RAISE","D_SINK","D_SHOCKWAVE"}
    deck = [c for c in all_cards if c.id in ids]
    rng.shuffle(deck)
    return deck, [], []

def draw_cards(state: GameState, n: int):
    for _ in range(n):
        if not state.deck:
            # reshuffle
            state.deck = state.discard
            state.discard = []
            state.rng.shuffle(state.deck)
            state.log.push("reshuffle", size=len(state.deck))
        if not state.deck:
            break
        state.hand.append(state.deck.pop())

def apply_effects(state: GameState, card: CardDef, target: Dict[str,Any]):
    log = state.log
    m = state.modifiers

    def set_turn_modifier(key, value):
        setattr(m, key, value)
        log.push("modifier_set", scope="turn", key=key, value=value)

    def add_turn_modifier(key, value):
        setattr(m, key, getattr(m, key) + value)
        log.push("modifier_add", scope="turn", key=key, value=value)

    def mul_turn_modifier(key, value):
        setattr(m, key, getattr(m, key) * value)
        log.push("modifier_mul", scope="turn", key=key, value=value)

    for ef in card.effects:
        t = ef["type"]
        if t == "add_temperature":
            x,y = target["cell"]
            before = state.grid[y][x].temperature
            state.grid[y][x].temperature = float(clamp(before + ef["amount"], -100, 100))
            log.push("temp_add", x=x,y=y, amount=ef["amount"], before=before, after=state.grid[y][x].temperature)
        elif t == "add_charge":
            x,y = target["cell"]
            before = state.grid[y][x].charge
            state.grid[y][x].charge = int(clamp(before + ef["amount"], -5, 5))
            log.push("charge_add", x=x,y=y, amount=ef["amount"], before=before, after=state.grid[y][x].charge)
        elif t == "add_charge_area":
            cx,cy = target["cell"]
            for yy in range(cy-1, cy+2):
                for xx in range(cx-1, cx+2):
                    if state.in_bounds(xx,yy):
                        before = state.grid[yy][xx].charge
                        state.grid[yy][xx].charge = int(clamp(before + ef["amount"], -5, 5))
                        log.push("charge_add", x=xx,y=yy, amount=ef["amount"], before=before, after=state.grid[yy][xx].charge, area="3x3")
        elif t == "set_turn_modifier":
            set_turn_modifier(ef["key"], ef["value"])
        elif t == "add_turn_modifier":
            add_turn_modifier(ef["key"], ef["value"])
        elif t == "mul_turn_modifier":
            mul_turn_modifier(ef["key"], ef["value"])
        elif t == "set_terrain":
            x,y = target["cell"]
            before = state.grid[y][x].terrain
            state.grid[y][x].terrain = ef["terrain"]
            log.push("terrain_set", x=x,y=y, before=before, after=ef["terrain"])
        elif t == "set_terrain_area":
            x0,y0 = target["cell"]
            for yy in range(y0, y0+2):
                for xx in range(x0, x0+2):
                    if state.in_bounds(xx,yy):
                        before = state.grid[yy][xx].terrain
                        state.grid[yy][xx].terrain = ef["terrain"]
                        log.push("terrain_set", x=xx,y=yy, before=before, after=ef["terrain"], area="2x2")
        elif t == "apply_status_area":
            x0,y0 = target["cell"]
            for yy in range(y0, y0+2):
                for xx in range(x0, x0+2):
                    if not state.in_bounds(xx,yy):
                        continue
                    uid = state.grid[yy][xx].unit_id
                    if not uid or uid not in state.units:
                        continue
                    u = state.units[uid]
                    if ef["status"] == "frozen":
                        u.frozen = max(u.frozen, int(ef.get("duration",2)))
                        log.push("status_apply", uid=uid, status="frozen", ttl=u.frozen, source=card.id)
        elif t == "heat_to_charge":
            x,y = target["cell"]
            tval = state.grid[y][x].temperature
            add = int(tval // ef.get("div",20))
            before = state.grid[y][x].charge
            state.grid[y][x].charge = int(clamp(before + add, -5, 5))
            log.push("heat_to_charge", x=x,y=y, t=tval, add=add, before=before, after=state.grid[y][x].charge)
        elif t == "steam_burst":
            x,y = target["cell"]
            cell = state.grid[y][x]
            if cell.terrain != "water" or cell.temperature < 50:
                log.push("steam_burst_fail", x=x,y=y, reason="not_water_or_temp<50")
                continue
            uid = cell.unit_id
            if uid and uid in state.units and state.units[uid].hp > 0:
                state.damage_unit(uid, int(ef.get("damage",12)), log, reason="steam_burst")
            state.enqueue_shockwave_center(x,y,steps=1,log=log,source="steam_burst")
            log.push("steam_burst", x=x,y=y)
        elif t == "push_unit":
            uid = target["unit"]
            dir_name = target["dir"]
            steps = int(ef.get("steps",1))
            state.enqueue_displacement(uid, dir_name, steps, log, source=card.id, collision_damage_override=ef.get("collision_damage_override", None))
        elif t == "shockwave":
            x,y = target["cell"]
            state.enqueue_shockwave_center(x,y,steps=int(ef.get("steps",1)),log=log,source=card.id)
        elif t == "add_height":
            x,y = target["cell"]
            before = state.grid[y][x].height
            state.grid[y][x].height = int(clamp(before + ef.get("amount",0), -1, 1))
            log.push("height_add", x=x,y=y, before=before, after=state.grid[y][x].height)
        elif t == "teleport_friend":
            uid = target["unit"]
            x,y = target["cell"]
            if state.grid[y][x].unit_id is not None:
                log.push("teleport_fail", uid=uid, to=(x,y), reason="occupied")
                continue
            u = state.units[uid]
            state.grid[u.y][u.x].unit_id = None
            state.grid[y][x].unit_id = uid
            u.x,u.y = x,y
            log.push("teleport", uid=uid, to=(x,y))
        elif t == "capacitor_store":
            x,y = target["cell"]
            c = state.grid[y][x]
            c.capacitor_value += c.charge
            c.capacitor_ttl = 1
            log.push("capacitor_store", x=x,y=y, amount=c.charge)
            c.charge = 0
        elif t == "place_trap":
            x,y = target["cell"]
            c = state.grid[y][x]
            c.trap_kind = ef.get("kind","static")
            c.trap_ttl = int(ef.get("duration",3))
            c.trap_charge_add = int(ef.get("charge_add",2))
            log.push("trap_place", x=x,y=y, kind=c.trap_kind, ttl=c.trap_ttl)
        elif t == "place_gravity_well":
            x,y = target["cell"]
            c = state.grid[y][x]
            c.gravity_well_ttl = int(ef.get("duration",2))
            log.push("gravity_well_place", x=x,y=y, ttl=c.gravity_well_ttl)
        elif t == "swap_charge":
            (x1,y1),(x2,y2) = target["cells"]
            c1 = state.grid[y1][x1].charge
            c2 = state.grid[y2][x2].charge
            state.grid[y1][x1].charge, state.grid[y2][x2].charge = c2, c1
            log.push("swap_charge", a=(x1,y1), b=(x2,y2), a_before=c1, b_before=c2)
        else:
            log.push("effect_unhandled", card=card.id, effect=ef)

def valid_targets(state: GameState, card: CardDef):
    # UI uses simple click-to-target; we validate at execution
    return True

def try_play_card(state: GameState, card: CardDef, click_cell: Tuple[int,int], dir_name: str, ui_selected_unit: Optional[str]=None, second_cell: Optional[Tuple[int,int]]=None):
    if state.ap < card.cost:
        state.log.push("card_fail", card_id=card.id, reason="no_ap")
        return False

    tgt = {}
    if card.targeting == "none":
        tgt = {}
    elif card.targeting == "cell":
        tgt = {"cell": click_cell}
    elif card.targeting == "area_2x2":
        tgt = {"cell": click_cell}  # top-left
    elif card.targeting == "area_3x3":
        tgt = {"cell": click_cell}  # center
    elif card.targeting == "unit_dir":
        # choose unit at clicked cell
        x,y = click_cell
        uid = state.grid[y][x].unit_id
        if not uid:
            state.log.push("card_fail", card_id=card.id, reason="no_unit")
            return False
        tgt = {"unit": uid, "dir": dir_name}
    elif card.targeting == "friend_to_empty":
        x,y = click_cell
        # if click on friend unit first -> pending; we do one-click heuristic: if click has friend, remember; else if click empty and have remembered in UI.
        uid = ui_selected_unit
        if uid is None:
            # try infer friend unit from clicked cell
            uid2 = state.grid[y][x].unit_id
            if uid2 and state.units[uid2].team == "player":
                # select unit, but not play yet
                return ("select_unit_for_teleport", uid2)
            state.log.push("card_fail", card_id=card.id, reason="select_friend_then_empty")
            return False
        if state.grid[y][x].unit_id is not None:
            state.log.push("card_fail", card_id=card.id, reason="dest_occupied")
            return False
        tgt = {"unit": uid, "cell": click_cell}
    elif card.targeting == "two_cells":
        # require two clicks; UI provides second_cell
        if second_cell is None:
            return ("need_second_cell", None)
        tgt = {"cells": [click_cell, second_cell]}
    else:
        state.log.push("card_fail", card_id=card.id, reason="unknown_targeting")
        return False

    state.ap -= card.cost
    state.log.push("card_play", card_id=card.id, target=tgt)
    apply_effects(state, card, tgt)
    # discard
    state.hand.remove(card)
    state.discard.append(card)
    return True

def is_stage_cleared(state: GameState) -> bool:
    # cleared when all enemies & boss dead
    for uid,u in state.units.items():
        if u.team in ("enemy","boss") and u.hp > 0:
            return False
    return True

def grant_reward(state: GameState, all_cards: List[CardDef]):
    # 3选1卡（这里简化：自动选择第一个，避免额外 UI）
    pool = [c for c in all_cards if c.id not in [cc.id for cc in state.deck+state.discard+state.hand]]
    if len(pool) < 3:
        pool = all_cards[:]
    # deterministic pick 3
    picks = []
    tmp = pool[:]
    state.rng.shuffle(tmp)
    picks = tmp[:3]
    chosen = picks[0]
    state.discard.append(chosen)
    state.log.push("reward_card", card_id=chosen.id, name=chosen.name)

def boss_roll_rule(state: GameState):
    if not state.boss_rule_pool:
        return
    pick = state.rng.choice(state.boss_rule_pool)
    state.log.push("boss_rule", desc=pick["desc"], id=pick["id"])
    # apply effects (reuse apply_effects-like primitives)
    for ef in pick.get("effects",[]):
        if ef["type"] == "set_turn_modifier":
            setattr(state.modifiers, ef["key"], ef["value"])
        elif ef["type"] == "add_turn_modifier":
            setattr(state.modifiers, ef["key"], getattr(state.modifiers, ef["key"]) + ef["value"])
        elif ef["type"] == "global_charge_pulse":
            state.modifiers.global_charge_pulse += int(ef.get("amount",0))
        else:
            state.log.push("boss_effect_unhandled", effect=ef)

def maybe_boss_every3(state: GameState):
    if not state.boss_every3 or state.turn % 3 != 0:
        return
    for efblock in state.boss_every3:
        for ef in efblock.get("effects",[]):
            if ef["type"] == "global_charge_pulse":
                amt = int(ef.get("amount",2))
                for y in range(state.h):
                    for x in range(state.w):
                        before = state.grid[y][x].charge
                        state.grid[y][x].charge = int(clamp(before + amt, -5, 5))
                state.log.push("global_charge_pulse", amount=amt)

def start_turn(state: GameState):
    state.ap = 3
    state.modifiers.reset_for_turn()
    state.log.push("turn_start", turn=state.turn)
    # boss rule roll
    if state.boss_uid and state.units.get(state.boss_uid) and state.units[state.boss_uid].hp > 0:
        boss_roll_rule(state)
        maybe_boss_every3(state)
    # capacitor release & traps decay at start? We follow your suggestion: capacitor releases at "下回合开始"
    physics.decay_cell_statuses(state, state.log)
    # draw
    draw_cards(state, 5 - len(state.hand))

def end_turn(state: GameState):
    state.log.push("turn_end", turn=state.turn)
    state.turn += 1

def resolve_physics(state: GameState):
    physics.apply_status_ticks(state, state.log)
    physics.temperature_resolve(state, state.log)
    physics.charge_resolve(state, state.log)
    physics.displacement_resolve(state, state.log)
    physics.decay_cell_statuses(state, state.log)

def make_snapshot(state: GameState) -> Dict[str,Any]:
    # deep snapshot for replay
    snap = {
        "turn": state.turn,
        "ap": state.ap,
        "grid": [[{
            "h": c.height, "t": c.temperature, "q": c.charge, "terrain": c.terrain, "unit": c.unit_id,
            "cap_v": c.capacitor_value, "cap_ttl": c.capacitor_ttl,
            "trap_k": c.trap_kind, "trap_ttl": c.trap_ttl, "trap_add": c.trap_charge_add,
            "gw": c.gravity_well_ttl
        } for c in row] for row in state.grid],
        "units": {uid:{
            "team": u.team, "x": u.x, "y": u.y, "hp": u.hp, "move": u.move,
            "burn": u.burning, "froz": u.frozen,
            "tags": list(u.tags), "water_regen": u.water_regen, "shock_mult": u.shock_taken_mult
        } for uid,u in state.units.items()},
        "deck": [c.id for c in state.deck],
        "discard": [c.id for c in state.discard],
        "hand": [c.id for c in state.hand],
        "mods": state.modifiers.__dict__.copy(),
        "boss_uid": state.boss_uid,
    }
    return snap

def restore_snapshot(state: GameState, snap: Dict[str,Any], all_cards_by_id: Dict[str,CardDef]):
    state.turn = snap["turn"]
    state.ap = snap["ap"]
    # grid
    state.grid = [[Cell() for _ in range(state.w)] for _ in range(state.h)]
    for y in range(state.h):
        for x in range(state.w):
            d = snap["grid"][y][x]
            c = state.grid[y][x]
            c.height = d["h"]; c.temperature = d["t"]; c.charge = d["q"]; c.terrain = d["terrain"]; c.unit_id = d["unit"]
            c.capacitor_value = d["cap_v"]; c.capacitor_ttl = d["cap_ttl"]
            c.trap_kind = d["trap_k"]; c.trap_ttl = d["trap_ttl"]; c.trap_charge_add = d["trap_add"]
            c.gravity_well_ttl = d["gw"]
    # units
    state.units = {}
    for uid,ud in snap["units"].items():
        u = Unit(uid=uid, team=ud["team"], x=ud["x"], y=ud["y"], hp=ud["hp"], move=ud["move"],
                 conductivity=1.0, weight="medium", tags=list(ud["tags"]),
                 water_regen=int(ud.get("water_regen",0)), shock_taken_mult=float(ud.get("shock_mult",1.0)))
        u.burning = ud["burn"]; u.frozen = ud["froz"]
        state.units[uid] = u
    state.deck = [all_cards_by_id[i] for i in snap["deck"]]
    state.discard = [all_cards_by_id[i] for i in snap["discard"]]
    state.hand = [all_cards_by_id[i] for i in snap["hand"]]
    # modifiers
    for k,v in snap["mods"].items():
        setattr(state.modifiers, k, v)
    state.disp_queue = []
    state.boss_uid = snap.get("boss_uid", None)

def run_game():
    root = os.path.dirname(os.path.dirname(__file__))
    data_dir = os.path.join(root, "data")
    all_cards = load_cards(os.path.join(data_dir,"cards.json"))
    all_cards_by_id = {c.id: c for c in all_cards}
    units_db = load_json(os.path.join(data_dir,"units.json"))
    encounters = load_json(os.path.join(data_dir,"encounters.json"))["stages"]

    seed = int(time.time()) & 0xFFFFFFFF
    rng = DeterministicRNG(seed)
    mods = TurnModifiers()
    mods.reset_for_turn()
    log = GameEventLog(seed=seed)
    state = GameState(modifiers=mods, log=log, rng=rng)
    ui = UI(state)

    log.push("meta_seed", seed=seed)
    stage_idx = 0

    # init deck
    state.deck, state.discard, state.hand = init_deck(all_cards, rng)
    draw_cards(state, 5)

    def start_stage(idx):
        enc = encounters[idx]
        init_stage(state, enc, units_db)
        state.stage_idx = idx
        state.log.push("stage_start", name=enc["name"], id=enc["id"])
        # keep deck/hand/discard across stages (roguelike run)
        state.disp_queue = []
        start_turn(state)
        # base snapshot for replay from stage start
        state.snapshot_base = make_snapshot(state)
        state.log.reset_cursor_end()

    start_stage(stage_idx)

    # for special targeting flows
    teleport_selected_uid = None
    swap_first_cell = None

    running = True
    while running:
        ui.tick()
        for ev in __import__("pygame").event.get():
            act = ui.handle_event(ev)
            if act == "quit":
                running = False
                break
            if isinstance(act, tuple):
                kind, payload = act
                if kind == "toggle_replay":
                    if payload:
                        # enter replay: reset to base snapshot and cursor=-1 then step forward to cursor
                        restore_snapshot(state, state.snapshot_base, all_cards_by_id)
                        state.log.cursor = -1
                    else:
                        # exit replay: no-op (stays at current state); for safety restore latest end state by re-sim? skipped.
                        pass
                if kind == "replay_step":
                    step = payload
                    if step < 0:
                        # step backward: restore and replay up to cursor-1
                        target = max(-1, state.log.cursor-1)
                        restore_snapshot(state, state.snapshot_base, all_cards_by_id)
                        # replay events up to target
                        for i in range(target+1):
                            apply_event_for_replay(state, state.log.events[i], all_cards_by_id)
                        state.log.cursor = target
                    else:
                        # step forward one if possible
                        if state.log.cursor + 1 < len(state.log.events):
                            state.log.cursor += 1
                            apply_event_for_replay(state, state.log.events[state.log.cursor], all_cards_by_id)
                if ui.replay_mode:
                    continue
                if kind == "end_turn":
                    # enemy turn then physics
                    ai.enemy_take_turn(state, state.log)
                    resolve_physics(state)
                    # check victory
                    if is_stage_cleared(state):
                        grant_reward(state, all_cards)
                        stage_idx += 1
                        if stage_idx >= len(encounters):
                            state.log.push("run_complete", note="all stages cleared")
                            running = False
                            break
                        start_stage(stage_idx)
                        teleport_selected_uid = None
                        swap_first_cell = None
                        ui.selected_card = None
                        continue
                    end_turn(state)
                    start_turn(state)
                    # update base snapshot for replay (turn start)
                    state.snapshot_base = make_snapshot(state)
                    state.log.reset_cursor_end()
                    teleport_selected_uid = None
                    swap_first_cell = None
                    ui.selected_card = None
                if kind == "play_card":
                    card = payload["card"]
                    cell = payload["cell"]
                    dirn = payload["dir"]

                    # handle multi-click targetings
                    if card.targeting == "friend_to_empty":
                        x,y = cell
                        uid_here = state.grid[y][x].unit_id
                        if teleport_selected_uid is None:
                            if uid_here and uid_here in state.units and state.units[uid_here].team == "player":
                                teleport_selected_uid = uid_here
                                state.log.push("teleport_select", uid=uid_here)
                            else:
                                state.log.push("card_fail", card_id=card.id, reason="select_friend_unit_first")
                            continue
                        else:
                            res = try_play_card(state, card, cell, dirn, ui_selected_unit=teleport_selected_uid)
                            if res is True:
                                teleport_selected_uid = None
                                ui.selected_card = None
                            continue

                    if card.targeting == "two_cells":
                        if swap_first_cell is None:
                            swap_first_cell = cell
                            state.log.push("swap_select_first", cell=cell)
                            continue
                        else:
                            res = try_play_card(state, card, swap_first_cell, dirn, second_cell=cell)
                            swap_first_cell = None
                            if res is True:
                                ui.selected_card = None
                            continue

                    res = try_play_card(state, card, cell, dirn)
                    if res is True:
                        ui.selected_card = None

        ui.draw()

def apply_event_for_replay(state: GameState, ev, all_cards_by_id):
    # Minimal replay: we apply only state-mutating events we log explicitly.
    # For determinism debugging, most important are cell/unit mutations.
    k = ev.kind
    d = ev.data
    if k == "turn_start":
        state.turn = d["turn"]
    if k == "modifier_set":
        setattr(state.modifiers, d["key"], d["value"])
    if k == "modifier_add":
        setattr(state.modifiers, d["key"], getattr(state.modifiers, d["key"]) + d["value"])
    if k == "modifier_mul":
        setattr(state.modifiers, d["key"], getattr(state.modifiers, d["key"]) * d["value"])
    if k == "temp_cell":
        x,y = d["x"], d["y"]
        state.grid[y][x].temperature = d["after"]
    if k == "temp_add":
        x,y = d["x"], d["y"]
        state.grid[y][x].temperature = d["after"]
    if k == "charge_add":
        x,y = d["x"], d["y"]
        state.grid[y][x].charge = d["after"]
    if k == "terrain_set":
        x,y = d["x"], d["y"]
        state.grid[y][x].terrain = d["after"]
    if k == "height_add":
        x,y = d["x"], d["y"]
        state.grid[y][x].height = d["after"]
    if k == "status_apply":
        uid = d["uid"]
        if uid in state.units:
            if d["status"] == "burning":
                state.units[uid].burning = max(state.units[uid].burning, d["ttl"])
            if d["status"] == "frozen":
                state.units[uid].frozen = max(state.units[uid].frozen, d["ttl"])
    if k == "unit_damage":
        uid = d["uid"]
        if uid in state.units and state.units[uid].hp > 0:
            state.units[uid].hp = max(0, state.units[uid].hp - d["amount"])
            if state.units[uid].hp == 0:
                state.grid[state.units[uid].y][state.units[uid].x].unit_id = None
    if k == "unit_die":
        uid = d["uid"]
        if uid in state.units:
            state.units[uid].hp = 0
    if k == "displace_move":
        uid = d["uid"]
        if uid in state.units and state.units[uid].hp > 0:
            fx,fy = d["from_"]
            tx,ty = d["to"]
            # clear from
            if 0 <= fy < state.h and 0 <= fx < state.w and state.grid[fy][fx].unit_id == uid:
                state.grid[fy][fx].unit_id = None
            # set to
            state.grid[ty][tx].unit_id = uid
            state.units[uid].x, state.units[uid].y = tx,ty
    if k == "teleport":
        uid = d["uid"]
        tx,ty = d["to"]
        if uid in state.units and state.units[uid].hp > 0:
            fx,fy = state.units[uid].x, state.units[uid].y
            if state.grid[fy][fx].unit_id == uid:
                state.grid[fy][fx].unit_id = None
            state.grid[ty][tx].unit_id = uid
            state.units[uid].x,state.units[uid].y = tx,ty
    if k == "swap_charge":
        (x1,y1) = d["a"]; (x2,y2) = d["b"]
        a_before = d["a_before"]; b_before = d["b_before"]
        # after swap
        state.grid[y1][x1].charge = b_before
        state.grid[y2][x2].charge = a_before
        # note: capacitor/trap/gravity_well details are omitted in replay for simplicity
