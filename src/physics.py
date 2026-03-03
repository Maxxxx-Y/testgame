from __future__ import annotations
from typing import Dict, List, Optional, Tuple
import heapq

from .util import DIR_ORDER, DIRS, clamp
from .model import TurnModifiers

def apply_status_ticks(state, log):
    # Burning tick (per round)
    burn_base = 6
    for uid,u in list(state.units.items()):
        if u.hp <= 0:
            continue
        if u.burning > 0:
            dmg = (burn_base + state.modifiers.burning_bonus_damage) * state.modifiers.temp_threshold_multiplier
            state.damage_unit(uid, int(dmg), log, reason="burning_tick")
            u.burning = max(0, u.burning - 1)
        if u.frozen > 0:
            u.frozen = max(0, u.frozen - 1)

def temperature_resolve(state, log):
    m = state.modifiers
    H = state.h
    W = state.w
    T0 = [[state.grid[y][x].temperature for x in range(W)] for y in range(H)]
    T1 = [[0.0 for _ in range(W)] for _ in range(H)]

    for y in range(H):
        for x in range(W):
            t = T0[y][x]
            out = t * m.temp_diffusion_factor
            deg = 0
            neigh = []
            for d in DIR_ORDER:
                dx,dy = DIRS[d]
                nx,ny = x+dx, y+dy
                if 0 <= nx < W and 0 <= ny < H:
                    deg += 1
                    neigh.append((nx,ny))
            share = out / deg if deg > 0 else 0.0
            # self after decay
            self_after = t * (1.0 - m.temp_decay_rate)
            T1[y][x] += self_after
            for nx,ny in neigh:
                T1[ny][nx] += share

    for y in range(H):
        for x in range(W):
            before = state.grid[y][x].temperature
            after = float(max(-100.0, min(100.0, T1[y][x])))
            state.grid[y][x].temperature = after
            if abs(after - before) > 1e-6:
                log.push("temp_cell", x=x, y=y, before=before, after=after)

    # thresholds scan (at end of temperature stage)
    for y in range(H):
        for x in range(W):
            cell = state.grid[y][x]
            uid = cell.unit_id
            if uid and uid in state.units and state.units[uid].hp > 0:
                u = state.units[uid]
                if cell.temperature >= 60:
                    u.burning = max(u.burning, 2)  # conservative ttl
                    log.push("status_apply", uid=uid, status="burning", ttl=2, source="temp_threshold")
                if cell.temperature <= -60:
                    u.frozen = max(u.frozen, 2)
                    log.push("status_apply", uid=uid, status="frozen", ttl=2, source="temp_threshold")
            # evaporation shockwave -> displacement queue
            if cell.terrain == "water" and cell.temperature >= 50:
                mult = m.temp_threshold_multiplier
                state.enqueue_shockwave_center(x, y, steps=1*mult, log=log, source="evaporation")
                log.push("evaporation", x=x, y=y, steps=1*mult)

def _dijkstra_path(state, start: Tuple[int,int], goal: Tuple[int,int], max_len: int):
    # deterministic Dijkstra with terrain costs; metal preferred (lower cost)
    W,H = state.w, state.h
    def terrain_cost(x,y):
        t = state.grid[y][x].terrain
        if t == "metal": return 0.5
        return 1.0

    sx,sy = start
    gx,gy = goal
    pq = []
    heapq.heappush(pq, (0.0, 0, sx, sy))
    prev = {(sx,sy): None}
    steps = {(sx,sy): 0}
    dist = {(sx,sy): 0.0}

    while pq:
        cost, step, x, y = heapq.heappop(pq)
        if (x,y) == (gx,gy):
            break
        if step >= max_len:
            continue
        if cost != dist.get((x,y), None):
            continue
        for d in DIR_ORDER:
            dx,dy = DIRS[d]
            nx,ny = x+dx, y+dy
            if 0 <= nx < W and 0 <= ny < H:
                nstep = step + 1
                if nstep > max_len:
                    continue
                ncost = cost + terrain_cost(nx,ny)
                key = (nx,ny)
                # tie-break deterministically by exploring DIR_ORDER and by heap ordering (cost, step, x, y)
                if (key not in dist) or (ncost < dist[key] - 1e-9) or (abs(ncost - dist[key]) < 1e-9 and nstep < steps[key]):
                    dist[key] = ncost
                    steps[key] = nstep
                    prev[key] = (x,y)
                    heapq.heappush(pq, (ncost, nstep, nx, ny))

    if (gx,gy) not in prev:
        return None
    # reconstruct
    path = []
    cur = (gx,gy)
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    return path

def charge_resolve(state, log):
    m = state.modifiers
    W,H = state.w, state.h

    # candidate edges in deterministic scan order
    candidates = []
    for y in range(H):
        for x in range(W):
            for d in DIR_ORDER:
                dx,dy = DIRS[d]
                nx,ny = x+dx, y+dy
                if not state.in_bounds(nx,ny):
                    continue
                c1 = state.grid[y][x].charge
                c2 = state.grid[ny][nx].charge
                if abs(c1 - c2) >= 2:
                    if c1 > c2:
                        candidates.append(((x,y),(nx,ny)))
                    elif c2 > c1:
                        candidates.append(((nx,ny),(x,y)))

    seen = set()
    for hi,lo in candidates:
        if (hi,lo) in seen:
            continue
        seen.add((hi,lo))
        hx,hy = hi
        lx,ly = lo
        delta = state.grid[hy][hx].charge - state.grid[ly][lx].charge
        if delta < 2:
            continue

        base_len = 1 + m.discharge_range_bonus
        water_bonus = 1 if (state.grid[hy][hx].terrain == "water" or state.grid[ly][lx].terrain == "water") else 0
        max_len = base_len + water_bonus

        path = _dijkstra_path(state, (hx,hy), (lx,ly), max_len=max_len)
        if path is None:
            path = [(hx,hy),(lx,ly)]
        # damage
        dmg = delta * 4
        if state.grid[hy][hx].temperature >= 40:
            dmg *= 1.5
        dmg *= m.discharge_damage_mult
        dmg_int = int(round(dmg))

        target_uid = state.grid[ly][lx].unit_id
        if target_uid and target_uid in state.units and state.units[target_uid].hp > 0:
            mult = state.units[target_uid].shock_taken_mult
            real = int(round(dmg_int * mult))
            state.damage_unit(target_uid, real, log, reason="discharge")
            log.push("discharge", from_=hi, to=lo, delta=delta, dmg=real, path=path, temp=state.grid[hy][hx].temperature)
        else:
            log.push("discharge", from_=hi, to=lo, delta=delta, dmg=0, path=path, temp=state.grid[hy][hx].temperature, note="no_unit")

def displacement_resolve(state, log):
    m = state.modifiers

    # gravity wells: at start of displacement stage
    for y in range(state.h):
        for x in range(state.w):
            cell = state.grid[y][x]
            if cell.gravity_well_ttl > 0:
                state.enqueue_gravity_pull(x,y,log,source="gravity_well")

    # process queue
    while state.disp_queue:
        ev = state.disp_queue.pop(0)
        uid = ev["uid"]
        if uid not in state.units or state.units[uid].hp <= 0:
            continue
        dir_name = ev["dir"]
        steps = int(ev.get("steps",1)) + m.displacement_steps_bonus
        collision_override = ev.get("collision_damage_override", None)

        # gravity map transform
        if m.gravity_map == "flip_y":
            if dir_name == "up": dir_name = "down"
            elif dir_name == "down": dir_name = "up"

        for s in range(steps):
            if uid not in state.units or state.units[uid].hp <= 0:
                break
            u = state.units[uid]
            dx,dy = DIRS[dir_name]
            nx,ny = u.x+dx, u.y+dy
            if not state.in_bounds(nx,ny):
                dmg = 3 + m.wall_damage_bonus + m.displacement_extra_damage
                state.damage_unit(uid, dmg, log, reason="wall_collision")
                log.push("displace_wall", uid=uid, from_=(u.x,u.y), dir=dir_name, dmg=dmg, source=ev.get("source",""))
                break
            to_cell = state.grid[ny][nx]
            if to_cell.unit_id is not None:
                other = to_cell.unit_id
                base = 4 if collision_override is None else collision_override
                dmg = base + m.displacement_extra_damage
                state.damage_unit(uid, dmg, log, reason="unit_collision")
                if other in state.units and state.units[other].hp > 0:
                    state.damage_unit(other, dmg, log, reason="unit_collision")
                log.push("displace_hit_unit", a=uid, b=other, at=(nx,ny), dmg=dmg, source=ev.get("source",""))
                break
            # move
            from_h = state.grid[u.y][u.x].height
            to_h = to_cell.height
            state.grid[u.y][u.x].unit_id = None
            to_cell.unit_id = uid
            u.x,u.y = nx,ny
            log.push("displace_move", uid=uid, from_=(nx-dx, ny-dy), to=(nx,ny), dir=dir_name, source=ev.get("source",""))
            # trap trigger
            if to_cell.trap_kind == "static" and to_cell.trap_ttl > 0:
                before = to_cell.charge
                to_cell.charge = int(clamp(to_cell.charge + to_cell.trap_charge_add, -5, 5))
                log.push("trap_trigger", kind="static", x=nx,y=ny, before=before, after=to_cell.charge, uid=uid)
            # height damage
            if abs(to_h - from_h) == 1:
                dmg = 5 + m.displacement_extra_damage
                state.damage_unit(uid, dmg, log, reason="height_diff")
                log.push("height_damage", uid=uid, at=(nx,ny), from_h=from_h, to_h=to_h, dmg=dmg)

def decay_cell_statuses(state, log):
    for y in range(state.h):
        for x in range(state.w):
            c = state.grid[y][x]
            if c.capacitor_ttl > 0:
                c.capacitor_ttl -= 1
                if c.capacitor_ttl == 0:
                    before = c.charge
                    c.charge = int(clamp(c.charge + c.capacitor_value, -5, 5))
                    log.push("capacitor_release", x=x,y=y, amount=c.capacitor_value, before=before, after=c.charge)
                    c.capacitor_value = 0
            if c.trap_ttl > 0:
                c.trap_ttl -= 1
                if c.trap_ttl == 0:
                    log.push("trap_expire", x=x,y=y, kind=c.trap_kind)
                    c.trap_kind = None
                    c.trap_charge_add = 0
            if c.gravity_well_ttl > 0:
                c.gravity_well_ttl -= 1
                if c.gravity_well_ttl == 0:
                    log.push("gravity_well_expire", x=x,y=y)
