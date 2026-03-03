from __future__ import annotations
from typing import List, Tuple
from .util import manhattan, DIR_ORDER, DIRS

def enemy_take_turn(state, log):
    # v0: enemies only move toward player (no attacks; avoids inventing enemy damage values)
    player = state.units.get("P1")
    if not player or player.hp <= 0:
        return
    for uid in list(state.turn_order_enemies()):
        u = state.units.get(uid)
        if not u or u.hp <= 0:
            continue
        # aquatic regen on water
        if "aquatic" in u.tags:
            cell = state.grid[u.y][u.x]
            if cell.terrain == "water":
                healed = min(u.water_regen, state.units_max_hp[uid]-u.hp)
                if healed > 0:
                    u.hp += healed
                    log.push("unit_heal", uid=uid, amount=healed, reason="aquatic_water_regen")
        steps = max(0, u.move - (1 if u.frozen > 0 else 0))
        for _ in range(steps):
            best = (manhattan((u.x,u.y),(player.x,player.y)), None)
            for dname in DIR_ORDER:
                dx,dy = DIRS[dname]
                nx,ny = u.x+dx, u.y+dy
                if not state.in_bounds(nx,ny):
                    continue
                if state.grid[ny][nx].unit_id is not None:
                    continue
                dist = manhattan((nx,ny),(player.x,player.y))
                cand = (dist, dname)
                if cand < best:
                    best = cand
            if best[1] is None:
                break
            state.move_unit(uid, best[1], 1, log, via="ai_move", no_damage=True)
