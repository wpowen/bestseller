"""Resumable topic workshop. Transport is injected by the CLI/web boundary."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
import fcntl
import json
from pathlib import Path
import random
import secrets
import time
from .filters import FIELDS, load, rotate, screen, shape
from .market import snapshot, features
from .recovery import StepFormatError, parse, read, save, step, update_topic

SCORES = ("不可补全", "点击欲", "长篇引擎", "好懂")


def axes_for(data, market):
    rng = random.Random(data["seed"])
    pools = load("pools")
    shells = list(
        dict.fromkeys(market.get("shells", []) + pools["shells"].get(data["genre"], pools["shells"]["default"]))
    )
    if len(shells) < 12:
        raise ValueError("熟壳池不足十二种")
    rng.shuffle(shells)
    shells = shells[:12] * 2
    rng.shuffle(shells)
    variables = [(group, v) for group, items in load("variables").items() for v in items]
    rng.shuffle(variables)
    pack = Path(__file__).resolve().parents[2] / "config/prompt_packs" / (data["genre"] + ".yaml")
    import yaml

    content = yaml.safe_load(pack.read_text("utf-8")) if pack.exists() else {}
    payoffs = list(dict.fromkeys(content.get("q1", {}).get("payoff_types", []) + pools["payoffs"]))
    axes = [
        dict(shell=s, variable=v, variable_type=g, payoff=rng.choice(payoffs), desire=rng.choice(pools["desires"]))
        for s, (g, v) in zip(shells, variables)
    ]
    for n, axis in enumerate(axes):
        axis["title_form"] = ("短名", "两段式", "反问感叹")[n % 3]
    direction = data.get("direction")
    if direction:
        source = direction["candidate"]["axes"]
        mode = direction["mode"]
        for n, axis in enumerate(axes):
            keep_shell = mode == "shell" or (mode == "both" and n % 6 < 3)
            axis["variant"] = "shell" if keep_shell else "variable"
            axis["shell" if keep_shell else "variable"] = source["shell" if keep_shell else "variable"]
            if keep_shell:
                if axis["variable"] == source["variable"]:
                    axis["variable"] = variables[-1][1]
            else:
                axis["variable_type"] = source.get("variable_type", "方向")
                if axis["shell"] == source["shell"]:
                    axis["shell"] = next(s for s in shells if s != source["shell"])
    return axes


def select(cards, data, ordered=False):
    if data.get("direction", {}).get("mode") == "both":
        attempts = []
        for order in (("shell", "variable"), ("variable", "shell")):
            chosen = []
            for variant in order:
                counts = Counter(shape(c["title"]) for c in chosen)
                chosen += rotate([x for x in cards if x["axes"].get("variant") == variant], 6, ordered, counts, 7)
            attempts.append(chosen)
        return max(attempts, key=len)
    return rotate(cards, 12, ordered)


def generation_prompt(data, axes, market):
    # Deliberate allow-list: no raw snapshot, anchors, blacklist or sample titles.
    categories = {k: market.get(k, []) for k in ("tags", "rules")}
    return (
        "你是中文网文创意编辑。只返回JSON数组。每组四轴写成一个具体故事，严格使用指定熟壳、唯一新变量、情绪型与欲望；"
        + "用户想法固定为主轴，其余轴围绕它变化。人物以具体职业、亲属或社会身份称谓辨识，称谓前后一致。每项按顺序对应一组轴，书名严格采用该组title_form结构，不添加第二个陌生概念。"
        + "除tags为数组外，所有字段均为有内容的字符串；无超能力时golden_finger_rule为字符串“无”。字段title（40字内，按卖点选两段式、反问式或短名，六项在三种书名形态间分配，每种至少一项、最多三项；反问式采用单句问句，短名采用无标点短语）、hook（30字内）、"
        + "tags（3至5个简体中文卖点）、premise（120字内，人物、欲望、阻碍）、golden_finger_rule（具体规则，没有写无）、"
        + "opening（一句开场画面）、engine（进度条和前三级问题梯，30章起必填，240字内）。全文简体中文。"
        + json.dumps(
            dict(
                题材=data["genre"],
                频道=data.get("channel", ""),
                章数=data["chapters"],
                想法=data["idea"],
                类别特征=categories,
                轴组合=axes,
            ),
            ensure_ascii=False,
        )
    )


def judge(folder, cards, anchors, data, client, name="judge"):
    prompt = (
        "你是选题判官，只评分不删候选。用不可补全、点击欲、长篇引擎、好懂四轴各0至10分。返回JSON数组，每项{id,scores:{四轴中文名:数字}}，覆盖全部id。"
        + json.dumps(
            dict(
                章数=data["chapters"],
                候选=[dict(id=i, **{k: c.get(k, "") for k in FIELDS}) for i, c in enumerate(cards)],
                同类市场锚点=anchors[:6],
            ),
            ensure_ascii=False,
        )
    )

    def validate(rows):
        if not isinstance(rows, list) or len(rows) != len(cards):
            raise ValueError("判官未覆盖全部候选")
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("判官字段")
            index, values = row.get("id"), row.get("scores")
            if (
                type(index) is not int
                or not 0 <= index < len(cards)
                or index in seen
                or not isinstance(values, dict)
                or set(values) != set(SCORES)
            ):
                raise ValueError("判官字段")
            if any(type(value) not in (int, float) or not 0 <= value <= 10 for value in values.values()):
                raise ValueError("判官分数")
            seen.add(index)

    rows = step(folder, name, prompt, client, 1800, validate)
    scores = {row["id"]: row["scores"] for row in rows}
    return [dict(candidate, scores=scores[index]) for index, candidate in enumerate(cards)]


def run(folder, client, market_root):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".workshop.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read(folder / "topic.json")
        if data.get("status") == "complete":
            return data
        started = time.monotonic()
        update_topic(folder, progress="市场数据")
        if "seed" not in data:
            data["seed"] = secrets.randbits(63)
            data["market"] = snapshot(market_root)
            data["features"] = features(data["market"], data["genre"], data.get("channel", ""))
            previous = []
            for p in folder.parent.glob("*/topic.json"):
                if p.parent == folder:
                    continue
                old = read(p, {})
                if old.get("status") == "complete" and old.get("genre") == data["genre"]:
                    previous.append((p.stat().st_mtime, old))
            data["history"] = [c for _, old in sorted(previous, reverse=True)[:5] for c in old.get("candidates", [])]
            data["axes"] = axes_for(data, data["features"])
            save(folder / "topic.json", data)
        raw = []

        def generate(n):
            axes = data["axes"][n * 6 : (n + 1) * 6]

            def validate(result):
                if not isinstance(result, list) or not 1 <= len(result) <= 6:
                    raise ValueError("候选数组无效：须返回1至6项")

            result = step(
                folder, f"generate-{n}", generation_prompt(data, axes, data["features"]), client, 4200, validate
            )
            return [
                dict(candidate, axes=axis) if isinstance(candidate, dict) else candidate
                for candidate, axis in zip(result, axes)
            ]

        # Publish the first valid group immediately; displayed card indices never change.
        batches, raw, visible = {}, [], list(data.get("candidates", []))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(generate, n): n for n in range(4)}
            update_topic(folder, progress="构思第 0/4 组")
            for future in as_completed(futures):
                batches[futures[future]] = future.result()
                raw = [card for n in sorted(batches) for card in batches[n]]
                cards, rejected = screen(
                    raw, data["chapters"], data["genre"], data["history"], data["market"].get("books", [])
                )
                for candidate in select(cards, data):
                    if len(visible) == 12 or any(c["title"] == candidate["title"] for c in visible):
                        continue
                    if sum(shape(c["title"]) == shape(candidate["title"]) for c in visible) >= 7:
                        continue
                    variant = candidate["axes"].get("variant")
                    if (
                        data.get("direction", {}).get("mode") == "both"
                        and sum(c["axes"].get("variant") == variant for c in visible) >= 6
                    ):
                        continue
                    shell = candidate["axes"]["shell"]
                    count = data["features"].get("shell_counts", {}).get(shell)
                    candidate["market_note"] = f"熟壳：{shell}；同壳新书：{count if count is not None else '未知'}"
                    visible.append(candidate)
                progress = f"构思第 {len(batches)}/4 组 → 筛选"
                patch = dict(progress=progress, candidates=visible, rejected=rejected, cost_usd=client.estimate())
                if visible:
                    patch.update(status="ready", judge_status="构思与筛选中，卡片已可选择")
                    if "first_cards_seconds" not in data:
                        data["first_cards_seconds"] = round(time.monotonic() - started, 3)
                    patch["first_cards_seconds"] = data["first_cards_seconds"]
                update_topic(folder, **patch)
        save(folder / "raw.json", raw)
        save(folder / "rejected.json", rejected)
        cards = visible
        for card in cards:
            shell = card["axes"]["shell"]
            count = data["features"].get("shell_counts", {}).get(shell)
            card["market_note"] = f"熟壳：{shell}；同壳新书：{count if count is not None else '未知'}"
        rows = read(folder / "http/ledger.json", [])
        reasons = dict(Counter(reason for row in rejected for reason in row["reasons"]))
        shortage = (
            (
                "；四组筛选后仍不足目标12个，字段/语言/名字/历史或形态配额限制："
                + json.dumps(reasons, ensure_ascii=False)
            )
            if len(cards) < 12
            else ""
        )
        data.update(
            status="ready",
            progress="排序（卡片已可选择）",
            first_cards_seconds=data.get("first_cards_seconds", round(time.monotonic() - started, 3)),
            candidates=cards,
            judge_status="排序中，卡片已可选择",
            rejected=rejected,
            cost_usd=client.estimate(),
            calls=len(rows),
            notice=f"实际展示{len(cards)}个；筛除{len(rejected)}个"
            + shortage
            + "。市场："
            + {"fresh": "当日快照", "stale": "最近可用缓存", "missing": "无市场数据"}.get(
                data["market"]["status"], data["market"]["status"]
            ),
        )
        data.pop("book_id", None)
        update_topic(folder, **data)
        try:
            ranked = judge(folder, cards, data["features"].get("anchors", []), data, client) if cards else []
            calibration = read(Path(market_root) / ".market" / ("judge-" + data["genre"] + ".json"), {})
            calibrated = calibration.get("valid") and calibration.get("date") == data["market"].get("date")
            ranking = sorted(range(len(ranked)), key=lambda i: -sum(ranked[i]["scores"].values())) if calibrated else []
            status = "已校准，分数仍为参考" if calibrated else "参考；尺子未通过或无当日校准，按多样性轮转"
        except StepFormatError:
            ranked, ranking = cards, []
            status = "判官格式失败，评分参考未知，按生成顺序展示"
        except Exception:
            # Ready cards remain usable if only the optional ranking request failed.
            update_topic(folder, judge_status="排序未完成，卡片仍可选择", progress="排序未完成")
            raise
        return update_topic(
            folder,
            status="complete",
            candidates=ranked,
            ranking=ranking,
            judge_status=status,
            progress="排序完成（分数仅供参考）",
            cost_usd=client.estimate(),
            calls=len(read(folder / "http/ledger.json", [])),
        )
