# Anti-Commonsense Hook Engine

Use this during `PLAN_PREMISE` before expanding BookSpec.

## Contract

1. Generate or select a structured `HookSpec`.
2. Score it with H_norm (`delta`, `reward`, `constraint`, `penalty`, `misunderstanding`, `expansion`, `learning_cost`).
3. Reject only if `H_norm < 15`; otherwise record warnings and strengthen weak fields.
4. Propagate the chosen HookSpec into:
   - `premise.md` / BookSpec `logline`, `reader_promise`, `series_engine`
   - `world.md` rules and `power_system.hard_limits`
   - `volume-plan.md` escalation and cost axes
   - chapter `methodology_contract.conflict_stakes` / `conflict_buffs`

## Minimum HookSpec Fields

```yaml
mechanism_key: forced_loss
base_desire: 赚钱
reversal: 必须亏损或放弃收益才能获得更高阶回报
rewards: [商业权限]
constraints:
  method: 必须让第三方真实受益
anti_cheat: [不能虚假交易]
costs: [现金流断裂]
misunderstanding: 所有人都以为主角败家
arc_engine: [亏损规模, 公开误判, 市场反噬]
one_liner: 主角想翻身，却必须越亏越强；每次赢来商业权限，也付出现实现金流代价。
core_rule: 每次获得回报都必须绑定限制、代价和反作弊压力。
```

## Strengthening Rules

- Weak reversal: make the protagonist's normal desire collide with a mandatory opposite action.
- Thin constraints: add time, object, method, ban, or explanation limits.
- Low cost: every successful use must create visible debt or aftereffect.
- No misunderstanding: add a durable public or interpersonal misread when the genre supports it.
