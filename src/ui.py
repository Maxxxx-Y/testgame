from __future__ import annotations
import pygame
from typing import Any, Dict, List, Optional, Tuple

from .util import DIR_ORDER

import os
import pygame

CELL = 64
MARGIN = 12
PANEL_W = 380
BOTTOM_H = 240
TOP_H = 44

# FONT_NAME = None
def load_cjk_font(size):
    root = os.path.dirname(os.path.dirname(__file__))
    font_path = os.path.join(root, "assets", "fonts", "NotoSansCJKsc-Regular.otf")

    if os.path.exists(font_path):
        return pygame.font.Font(font_path, size)

    # fallback (should not happen in release)
    return pygame.font.SysFont("Arial", size)

class UI:
    def __init__(self, state):
        pygame.init()
        self.state = state
        w = MARGIN*2 + CELL*state.w + PANEL_W
        h = MARGIN*2 + TOP_H + CELL*state.h + BOTTOM_H
        self.screen = pygame.display.set_mode((w,h))
        pygame.display.set_caption("Entropy Field Demo")
        self.font = load_cjk_font(18)
        self.font_small = load_cjk_font(14)
        self.clock = pygame.time.Clock()
        self.scroll = 0

        self.selected_card = None
        self.pending_dir = "up"
        self.hover_cell = None

        self.replay_mode = False

    def grid_origin(self):
        return (MARGIN, MARGIN + TOP_H)

    def cell_rect(self, x,y):
        ox,oy = self.grid_origin()
        return pygame.Rect(ox + x*CELL, oy + y*CELL, CELL, CELL)

    def point_to_cell(self, pos):
        ox,oy = self.grid_origin()
        px,py = pos
        gx = (px - ox) // CELL
        gy = (py - oy) // CELL
        if 0 <= gx < self.state.w and 0 <= gy < self.state.h:
            return int(gx), int(gy)
        return None

    def draw(self):
        s = self.screen
        s.fill((20,20,24))
        self.draw_topbar()
        self.draw_grid()
        self.draw_panel()
        self.draw_hand()
        pygame.display.flip()

    def draw_topbar(self):
        s = self.screen
        txt = f"Stage: {self.state.stage_name}   Turn: {self.state.turn}   AP: {self.state.ap}   Deck:{len(self.state.deck)} Discard:{len(self.state.discard)}"
        if self.replay_mode:
            txt += f"   [REPLAY] {self.state.log.cursor+1}/{len(self.state.log.events)}"
        t = self.font.render(txt, True, (230,230,230))
        s.blit(t, (MARGIN, MARGIN))

    def draw_grid(self):
        s = self.screen
        ox,oy = self.grid_origin()
        for y in range(self.state.h):
            for x in range(self.state.w):
                r = self.cell_rect(x,y)
                c = self.state.grid[y][x]
                # terrain base
                col = (48,48,52)
                if c.terrain == "water": col = (35,45,70)
                if c.terrain == "metal": col = (60,60,70)
                if c.terrain == "fragile": col = (70,45,45)
                pygame.draw.rect(s, col, r)
                pygame.draw.rect(s, (80,80,90), r, 1)

                # values
                t_int = int(round(c.temperature))
                text = self.font_small.render(f"T{t_int:>4}", True, (240,240,240))
                s.blit(text, (r.x+4, r.y+4))
                text2 = self.font_small.render(f"Q{c.charge:>2}", True, (240,240,240))
                s.blit(text2, (r.x+4, r.y+22))
                text3 = self.font_small.render(f"H{c.height:+d}", True, (240,240,240))
                s.blit(text3, (r.x+4, r.y+40))

                # unit
                if c.unit_id:
                    u = self.state.units.get(c.unit_id)
                    if u and u.hp > 0:
                        # circle marker
                        color = (90,220,140) if u.team == "player" else (220,90,90) if u.team=="enemy" else (200,120,220)
                        pygame.draw.circle(s, color, (r.centerx, r.centery), 10)
                        # hp bar
                        maxhp = self.state.units_max_hp.get(u.uid, u.hp)
                        w = int((u.hp/maxhp) * (CELL-8))
                        pygame.draw.rect(s, (0,0,0), pygame.Rect(r.x+4, r.bottom-10, CELL-8, 6))
                        pygame.draw.rect(s, (230,230,230), pygame.Rect(r.x+4, r.bottom-10, w, 6))
                        # statuses
                        st = []
                        if u.burning>0: st.append("B")
                        if u.frozen>0: st.append("F")
                        if st:
                            ts = self.font_small.render("".join(st), True, (255,220,120))
                            s.blit(ts, (r.right-18, r.y+4))

        # hover
        if self.hover_cell:
            x,y = self.hover_cell
            pygame.draw.rect(s, (255,255,255), self.cell_rect(x,y), 2)

    def draw_panel(self):
        s = self.screen
        ox = MARGIN*2 + CELL*self.state.w
        oy = MARGIN + TOP_H
        area = pygame.Rect(ox, oy, PANEL_W-MARGIN, CELL*self.state.h)
        pygame.draw.rect(s, (28,28,32), area)
        pygame.draw.rect(s, (80,80,90), area, 1)

        # log lines (latest last)
        lines = self.state.format_log_lines()
        # scrolling
        max_lines = int(area.height / 18) - 2
        start = max(0, len(lines) - max_lines - self.scroll)
        view = lines[start:start+max_lines]
        y = area.y + 8
        title = self.font.render("结算日志", True, (230,230,230))
        s.blit(title, (area.x+8, y))
        y += 26
        for ln in view:
            t = self.font_small.render(ln, True, (220,220,220))
            s.blit(t, (area.x+8, y))
            y += 18

    def draw_hand(self):
        s = self.screen
        ox,oy = MARGIN, MARGIN + TOP_H + CELL*self.state.h + 12
        area = pygame.Rect(ox, oy, CELL*self.state.w + PANEL_W - MARGIN, BOTTOM_H-24)
        pygame.draw.rect(s, (24,24,28), area)
        pygame.draw.rect(s, (80,80,90), area, 1)

        title = self.font.render("手牌（点击出牌） | Space:结束回合 | 方向键：方向类卡牌 | R:回放", True, (230,230,230))
        s.blit(title, (area.x+8, area.y+8))

        # show selected card
        if self.selected_card:
            sc = self.selected_card
            info = f"已选：{sc.name} (AP{sc.cost})  目标:{sc.targeting}  方向:{self.pending_dir if 'dir' in sc.targeting else '-'}"
            t = self.font_small.render(info, True, (255,220,140))
            s.blit(t, (area.x+8, area.y+34))
            d = self.font_small.render(sc.desc, True, (220,220,220))
            s.blit(d, (area.x+8, area.y+54))
        else:
            t = self.font_small.render("未选卡牌", True, (200,200,200))
            s.blit(t, (area.x+8, area.y+34))

        # draw cards as buttons
        x = area.x + 8
        y = area.y + 80
        bw,bh = 150, 56
        for i,card in enumerate(self.state.hand):
            r = pygame.Rect(x + (i%6)*(bw+8), y + (i//6)*(bh+8), bw, bh)
            col = (48,48,56)
            if self.selected_card and self.selected_card.id == card.id:
                col = (70,60,40)
            pygame.draw.rect(s, col, r, border_radius=6)
            pygame.draw.rect(s, (90,90,100), r, 1, border_radius=6)
            name = self.font_small.render(f"{card.name}", True, (240,240,240))
            s.blit(name, (r.x+8, r.y+6))
            cost = self.font_small.render(f"AP {card.cost}", True, (220,220,220))
            s.blit(cost, (r.x+8, r.y+28))
            axis = self.font_small.render(f"{card.axis}", True, (180,200,255))
            s.blit(axis, (r.right-54, r.y+28))

        self.hand_area = area

    def card_at_pos(self, pos):
        if not hasattr(self, "hand_area"):
            return None
        area = self.hand_area
        x = area.x + 8
        y = area.y + 80
        bw,bh = 150, 56
        for i,card in enumerate(self.state.hand):
            r = pygame.Rect(x + (i%6)*(bw+8), y + (i//6)*(bh+8), bw, bh)
            if r.collidepoint(pos):
                return card
        return None

    def handle_event(self, ev):
        if ev.type == pygame.QUIT:
            return "quit"
        if ev.type == pygame.MOUSEMOTION:
            self.hover_cell = self.point_to_cell(ev.pos)
        if ev.type == pygame.MOUSEWHEEL:
            # scroll log
            if ev.y > 0:
                self.scroll = min(self.scroll + 1, 9999)
            elif ev.y < 0:
                self.scroll = max(self.scroll - 1, 0)
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_r:
                self.replay_mode = not self.replay_mode
                return ("toggle_replay", self.replay_mode)
            if self.replay_mode:
                if ev.key == pygame.K_LEFTBRACKET:
                    return ("replay_step", -1)
                if ev.key == pygame.K_RIGHTBRACKET:
                    return ("replay_step", +1)
                return None
            # normal mode
            if ev.key == pygame.K_SPACE:
                return ("end_turn", None)
            if ev.key == pygame.K_UP:
                self.pending_dir = "up"
            if ev.key == pygame.K_RIGHT:
                self.pending_dir = "right"
            if ev.key == pygame.K_DOWN:
                self.pending_dir = "down"
            if ev.key == pygame.K_LEFT:
                self.pending_dir = "left"
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            if self.replay_mode:
                return None
            card = self.card_at_pos(ev.pos)
            if card:
                self.selected_card = card
                return None
            # play card if selected and click on grid
            cell = self.point_to_cell(ev.pos)
            if self.selected_card and cell:
                return ("play_card", {"card": self.selected_card, "cell": cell, "dir": self.pending_dir})
        return None

    def tick(self, fps=60):
        self.clock.tick(fps)
