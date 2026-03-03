# Entropy Field Demo (Windows / Python)

一个**可运行的 8×8 单机肉鸽 Demo**，按你给的规格实现：

- 8×8 沙盘：height / temperature(float) / charge(int) / terrain / unit
- 回合流程：抽 5 → AP=3 → 玩家出牌 → 敌人行动（仅移动，**不主动攻击**）→ 物理结算（温度→电荷→位移）
- 三轴物理严格顺序结算，均为确定性算法（含双缓冲）
- 30 张卡（温度 10 / 电荷 10 / 位移 10），数据驱动（JSON）
- 必备调试：结算日志 + 每格数值可视化（T/Q/H）+ 事件回放步进

> 说明：你方案里敌人攻击伤害、玩家基础属性未给出。为避免“凭空加机制”，本 Demo 采取**最保守实现**：
> - 敌人回合只做“朝玩家靠近”的移动，不攻击。
> - 玩家单位存在但仅用于被位移/被伤害等通用逻辑；HP/Move 等基础数值放在 data/units.json，可自行改成你最终真值。

---

## 1) Windows 运行方式

### 方式 A：直接双击
1. 安装 Python 3.10+（建议 3.11/3.12）
2. 在项目目录双击 `run.bat`

### 方式 B：命令行
```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

---

## 2) 操作说明（键鼠）

- 鼠标点击手牌卡牌 → 进入“出牌模式”
- 根据卡牌提示点击目标格/单位
- **方向类卡牌**：选中卡牌后按方向键（↑→↓←）设置方向，再点击单位
- 结束回合：`Space`
- 回放单步：
  - `[` 上一步事件
  - `]` 下一步事件
  - `R` 切换回放/实时模式（回放模式下不允许操作，只看事件逐条复现）
- 日志滚动：鼠标滚轮

---

## 3) 数据驱动入口

- `data/cards.json`：30 张卡
- `data/encounters.json`：3 战 + Boss
- `data/relics.json`：3 个示例遗物（仅修改已存在的全局参数）
- `data/units.json`：玩家与敌人模板

---

## 4) 重要实现约束（与你文档一致）

- 温度：扩散 out = T*diffFactor（默认0.5），邻居均分；衰减保留 (1-decayRate)（默认0.5）；双缓冲
- 阈值扫描：T>=60 burning；T<=-60 frozen；water & T>=50 蒸发→位移事件（进入位移队列）
- 放电：相邻电位差>=2 触发；路径 metal 优先（低代价）；确定性 tie-break（上右下左）；伤害=delta*4，温度>=40 则×1.5
- 放电 **不改变 charge**（你文档未定义转移规则，按最保守实现）
- 位移：事件队列；撞墙 3 伤；落差(|Δh|=1) 5 伤；相撞双方 4 伤（强制冲撞覆盖为8）

---

## 5) 目录结构

- `main.py`：入口
- `src/`：核心逻辑
  - `game.py`：主循环与状态机（战斗/奖励/进度）
  - `model.py`：数据结构（Cell/Unit/Modifiers/EventLog）
  - `cards.py`：卡牌系统（JSON → Effects）
  - `physics.py`：三轴结算器
  - `ai.py`：敌人 AI（仅移动）
  - `ui.py`：Pygame UI（网格+手牌+日志+回放）
  - `util.py`：小工具（确定性随机、夹取等）
- `data/`：数据

---

## 6) 你要改数值，只改数据文件即可

你强调“文档为真值”，所以项目里把可疑/不确定项都放在 `data/`：
- 玩家/敌人 HP、move、conductivity、weight
- Boss 规则列表（每回合抽 1 个，持续 1 回合）
- 遗物（永久修改全局参数）
