"""Reproducible offline HTTP fixture: five books, 150 chapters, no Queue worker.

Run from the repository root with the project's Python:
    PYTHONPATH=engine python -B engine/tests/fixtures/books_benchmark.py --output /tmp/books-summary.json
Cold creates a fresh Library; it does not flush the OS page cache.
"""

import argparse
from contextlib import contextmanager
import hashlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import math
from pathlib import Path
import platform
import re
import statistics
import sys
import tempfile
import threading
import time

ENGINE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ENGINE / "tools/webui"), str(ENGINE)]
from library import Library
from server import Handler

PASSAGES = [
    "雨从傍晚下到深夜，河埠头的石阶已经看不出原来的颜色。林舟把最后一捆麻绳收进棚里，忽然听见对岸有人喊他的名字。那声音隔着水，断断续续。他提起灯，照见浮在水面的一只木箱。箱角撞着桥墩，发出空空的响声。\n"
    "“别下去。”陈婶从门里探出半个身子，手里还攥着没缝完的布鞋，“今晚水涨得快，连熟水性的也站不稳。”林舟把绳子绕在腰间，另一头递给她。“我不游过去，只把箱子钩上来。你替我看着后面的桥洞。”陈婶没有接话，先把灯挪到了高处。\n"
    "木箱比他想的沉。竹竿一碰到铜环，他就听见里面有东西滚动。不是石头，也不像货栈丢失的铁器。他俯身拉住箱沿，袖口立刻浸透了。陈婶抓着绳子后退，两个人一寸一寸地把箱子拖上石阶。箱底磨过青苔，留下一道浅白色的痕。\n",
    "林舟认得箱盖上的刻痕。父亲修船时总爱在木料边缘留三道斜口，好与别家的存货分开。可父亲的船三年前便沉在了上游，连船牌都没找回来。他蹲在箱前，指腹从那几道刻痕上擦过去，掌心沾了细细的泥沙。“拿锤子来。”他说。\n"
    "陈婶去柜台后面找工具，脚步忽然停住。门槛上多了一双沾泥的鞋，一名陌生少年站在雨里，怀中抱着油布包。他的左手紧紧按着右肩，血顺着指缝落在衣角。“不能砸，”少年说，“里面的人托我带句话，东西交到你手里以前，谁都不能开。”\n"
    "“你先把伤口按住。”林舟抽出一条干净布巾，扔给少年，又让陈婶把炭盆搬来。他没有追问对方的姓名，只将箱子转了个方向，让锁孔朝着火光。锁眼里塞着一小片薄铜，边缘被人磨得很亮。他用镊子夹住铜片，手腕轻轻一转，锁舌便缩了回去。\n",
    "箱中没有金银，只有一件褪色的棉袄，几封被油纸裹住的信，以及半张渡船时刻表。林舟展开最上面那封，信纸上没有落款。第一行字写着今年春天才改过的新埠头名。他看了两遍，才把信放到桌上。父亲的字他认得，可写信的人显然还活在现在。\n"
    "少年坐在炭盆旁，脸色终于缓过来。他说自己在南岸的芦苇荡里见过送箱的人，对方穿着旧船工的短褂，右腿走路不利索。“他叫我赶在子时以前送到，说你要是问起，就提一提屋后的枣树。”林舟抬起头。那棵树早在搬家以前就砍了，邻居也很少知道。\n"
    "陈婶拿起时刻表，对着灯看了一阵，忽然用手指点住末尾一行。“这班船根本没开过。前年冰灾以后，北渡的码头就封了。”她从柜子里找出一本旧账，把两页纸并在一起。船名相同，开船的日子却错了整整七天。有人故意把日期往后推，让来找船的人错过涨潮。\n",
    "林舟把三封信按纸张的旧色重新排好。前两封说的是寻常家事，最后一封却只画了一幅简图。图上有桥，有废弃的盐仓，还有一条从仓后通向河边的小路。他拿起笔，在桥下添了一个圆圈。“不是去北渡，我们去这里。水一退，桥洞下面就能站人。”\n"
    "少年想跟着起身，肩膀刚动便疼得吸了口气。林舟按住他的椅背，让他留在店里等大夫。陈婶已经把绳索、火折子和一小包干粮放进布袋。“你回来以前，我不关门。”她说。林舟点头，将那封画着简图的信贴身收好，吹灭了柜台边的小灯。\n"
    "门外的雨细了些。街角卖面的摊子正在收伞，热汤的气味混着河水的腥气飘过来。林舟没有沿大路走，转进了靠河的窄巷。他记得小时候父亲带他从这里抄近路，墙头伸着石榴枝，巷尾总有人晒渔网。如今渔网还在，晾网的人却早已换了。\n",
    "到了盐仓外，他先绕着围墙走了一圈。后门的铁链完整，地上的泥却被踩乱了，其中一道车辙一直延伸到水边。林舟蹲下来，用灯照向车辙末端。泥里压着一枚衣扣，扣背缠了一缕蓝线。那是货栈伙计常穿的短衣，今天傍晚他还在街上见过。\n"
    "桥洞里传来咳嗽声。林舟收起灯，沿墙根慢慢靠近。黑暗中有人把一块石子扔进河里，随后响起低低的问话：“东西拿到了？”他没有回答，先将绳头系在桥边的木桩上。父亲教过他，水上见不着底的时候，先给自己留一条能摸回去的路。\n"
    "一盏灯在桥下亮起来，照出了半张熟悉的脸。那人把手中的账册抬高，纸页已经湿得卷了边。“你父亲让我等你。”林舟看着对方的眼睛，伸出的手停在半空。“先告诉我，三年前最后一趟船上，究竟坐了几个人？”河水拍着桥墩，灯影随之轻轻晃动。\n",
    "那人没有立刻回答。他先把灯放在干燥的石台上，从怀里取出一张折了几道的船票。票根右下角缺了一块，露出里面细密的棉纤维。“原本是十二个人，开船前又上来了一个。”林舟记得官府告示上写的是十一人。他伸手要拿船票，对方却把手缩了回去。\n"
    "“我不是来买消息的。”林舟说。他解下布袋，把干粮和火折子放在石台另一端。“你愿意说，我就听。要是还想让我替你做别的事，先把人名讲清楚。”那人盯着布袋看了片刻，终于低头掰开一个饼。饼心仍有一点暖意，他咬了一口，喉头动了好几下。\n"
    "桥上忽然传来车轮声。林舟抬手压低灯罩，两个人同时收了声。车子在盐仓门前停住，有人下车敲了三下门，间隔很短。接着是铁链被拖动的声音。林舟顺着石缝望出去，只能看见半截裤腿和车轮旁的一双皮靴。赶车的人在催，说天亮前还得过城门。\n"
    "等车轮再次滚动，那人才把船票递过来。“多出来的是个孩子，没有记在名册上。你父亲把他藏在舱里，船出事时，先把他送上了岸。”林舟捏着湿软的纸，没有问孩子后来去了哪里。他想起家中那件棉袄，袖口被改短过，里子上还有一块颜色不同的补丁。\n"
    "“你知道他的名字？”他问。那人摇头，只记得孩子一直抱着一个铜饭盒，盒盖上刻着两只鸟。林舟从袋里抽出纸笔，将鸟的样子画下来。画到第二只时，对方伸手指出翅膀的方向。他便划掉重画，直到那人点头，才把纸折好，和船票放在一起。\n"
    "河水已经退到石阶下面，远处响起报更的梆子。林舟让那人先回陈婶的店，自己留下来再看一眼仓门。他不愿凭一张船票就认定父亲的去向，但盐仓里今晚确实有人，官府的名册也确实少了名字。这两件事足够他明天去找当年的船主，当面问清楚。\n"
    "离开桥洞前，他从木桩上解下绳子，重新绕在肩头。灯罩里积了一层水汽，他用袖子擦去，脚下的石路顿时亮了许多。街口已经有卖早饭的人挑着担子过来，竹篮里散出新蒸馒头的香气。林舟买了两个，揣在怀里，沿着来时的窄巷往回走。\n",
]


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(content, encoding="utf-8")


def ledger_row(stage, unknown=False):
    return dict(
        stage=stage,
        model="MiniMax-M3.1-Flash",
        status="unknown" if unknown else "ok",
        input_bytes=18000,
        output_token_cap=4096,
        usage=None if unknown else dict(prompt_tokens=4200, completion_tokens=2600),
    )


def fixtures(root):
    own = root / "webui"
    (own / ".archive").mkdir(parents=True)
    put(own / ".spent.json", {"webui/deleted-example": {"charged": 0.125}})
    cjk = []
    final_counts = [30, 29, 25, 20, 15]
    stage_states = ["complete", "running", "waiting", "quota_exhausted", "error"]
    for index in range(5):
        folder = own / f"fixture-{index + 1}"
        state = stage_states[index]
        put(
            folder / "run.json",
            dict(
                status="complete",
                started=f"2026-11-0{index + 1}T00:00:00Z",
                book=dict(chapters=30, words_per_chapter=2200, prompt_pack="cozy-mystery"),
                execution_seconds=7200,
                wall_seconds=8000,
            ),
        )
        put(
            folder / "web.json",
            dict(
                title=f"雨夜渡口{index + 1}",
                chapters=30,
                genre="cozy-mystery",
                created=f"2026-11-0{index + 1}T00:00:00Z",
                status="complete" if index == 0 else "running",
                polish=True,
                auto_deai=True,
                pipeline_version=1,
                pipeline=dict(book=dict(status="complete"), polish=dict(status=state)),
            ),
        )
        put(
            folder / "story_bible.yaml",
            dict(
                title=f"雨夜渡口：第{index + 1}部",
                premise="船工之子从河中木箱找到失踪父亲的来信，追查三年前渡船上的秘密。",
                characters=[dict(name="林舟", role="主角", desire="找回父亲"), dict(name="陈婶", role="渡口店主")],
            ),
        )
        put(folder / "book.yaml", dict(chapters=30, words_per_chapter=2200, prompt_pack="cozy-mystery"))
        put(folder / "runtime.json", dict(kind="running", stage="complete", updated=1793836800))
        put(
            folder / ".pipeline/polish/runtime.json",
            dict(
                kind=state,
                stage="recheck",
                retry=2,
                next_retry_at=1793836860 if state == "waiting" else None,
                error={
                    "waiting": "接口限流，等待重试",
                    "quota_exhausted": "模型账户额度不足",
                    "error": "离线示例处理异常",
                }.get(state, ""),
                updated=1793836800,
            ),
        )
        calls = [ledger_row(f"planning-{n}") for n in range(3)]
        for number in range(1, 31):
            # Written Chinese narrative, with chapter context; exactly 2200 Unicode characters.
            intro = f"林舟走到渡口时，已经是这一程的第{number}天。陈婶给他留了热饭，他顾不上吃，先去看河边的船。\n\n"
            body = (intro + "\n".join(PASSAGES))[:2200]
            assert len(body) == 2200
            cjk.append(len(re.findall(r"[\u3400-\u9fff]", body)))
            put(folder / f"chapter-{number:03d}.body.md", body)
            put(
                folder / f"chapters/{number:03d}.json",
                dict(
                    number=number,
                    title=f"渡口来信·{number}",
                    status="complete",
                    words=cjk[-1],
                    within_band=True,
                    hard_conflicts=[],
                    remaining=[],
                    warnings=["人物称谓需抽读"] if number == 7 else [],
                    state_issues=[],
                    quality_issues=[],
                ),
            )
            calls.extend(ledger_row(f"chapter-{number}-{stage}") for stage in ("outline", "draft", "check", "state"))
            put(
                folder / f"writing_outlines/{number:03d}.json",
                dict(
                    title=f"渡口来信·{number}",
                    scene="林舟查验来信，核对渡船时刻，决定前往盐仓。",
                    characters=["林舟", "陈婶"],
                ),
            )
            if number <= final_counts[index]:
                put(folder / f"final/chapter-{number:03d}.md", body.replace("已经是", "正好是", 1))
                retained = number % 9 == 0
                put(
                    folder / f"final/chapter-{number:03d}.deai.json",
                    dict(
                        status="retained" if retained else "polished",
                        complete=not retained,
                        processing_complete=True,
                        before_score=6.2,
                        after_score=7.3,
                        chapter=number,
                        source_hash=hashlib.sha256(body.encode()).hexdigest(),
                        findings=[dict(rule="重复句式", paragraph=2, repaired=not retained)],
                        reason="复检未通过，保留原稿" if retained else "局部修复完成",
                    ),
                )
                put(
                    folder / f"final/.deai/{number:03d}/http/ledger.json",
                    [
                        ledger_row(f"{number}-{stage}", unknown=(index == 4 and number == 15 and stage == "recheck"))
                        for stage in ("detect", "repair", "recheck")
                    ],
                )
        put(folder / "calls.json", calls)
        put(folder / "http/ledger.json", [ledger_row("conception"), ledger_row("bible")])
    for index in range(2):
        folder = own / ".topics" / f"00000000-0000-4000-8000-{index + 1:012d}"
        put(
            folder / "topic.json",
            dict(
                id=folder.name,
                status="complete",
                genre="cozy-mystery",
                chapters=30,
                idea="渡口来信",
                candidates=[dict(title="雨夜渡口", premise="船工寻找父亲")],
            ),
        )
        put(folder / "http/ledger.json", [ledger_row(f"generate-{n}") for n in range(6)])
        put(folder / "runtime.json", {})
    return dict(
        books=5,
        chapters=150,
        body_chars=2200,
        body_cjk_min=min(cjk),
        body_cjk_max=max(cjk),
        final_chapters=sum(final_counts),
        topic_batches=2,
        archived_books=0,
        root_http_ledgers=5,
        final_http_ledgers=sum(final_counts),
        files=sum(p.is_file() for p in root.rglob("*")),
    )


@contextmanager
def http_library(root):
    """Start only an HTTP accept loop on an ephemeral loopback port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.origin = f"http://127.0.0.1:{server.server_address[1]}"
    server.hosts = {server.origin.removeprefix("http://")}
    server.library = Library(root, read_only=True)
    server.queue = None
    server.configuration = {}
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def get_json(server, path="/api/books", expected_status=200):
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        raw = response.read()
        assert response.status == expected_status, (response.status, raw)
        return json.loads(raw)
    finally:
        connection.close()


def summary(samples):
    ordered = sorted(samples)
    return dict(
        count=len(samples),
        samples_ms=samples,
        median_ms=statistics.median(samples),
        mean_ms=statistics.mean(samples),
        p95_ms=ordered[math.ceil(len(samples) * 0.95) - 1],
        min_ms=min(samples),
        max_ms=max(samples),
    )


def benchmark(root, cold_count=3, warm_count=20):
    fixture = fixtures(root)
    digest = None
    with http_library(root) as server:

        def request():
            nonlocal digest
            start = time.perf_counter_ns()
            payload = get_json(server)
            elapsed = (time.perf_counter_ns() - start) / 1e6
            fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if digest is not None:
                assert fingerprint == digest, "Immutable fixture response changed"
            digest = fingerprint
            assert len(payload["books"]) == 5
            assert sum(b["total"] for b in payload["books"]) == 150
            assert sum(b["written_chapters"] for b in payload["books"]) == 150
            assert sum(b["chapters"] for b in payload["books"]) == fixture["final_chapters"]
            assert payload["spending"] > 0
            return elapsed

        cold = []
        for _ in range(cold_count):
            server.library = Library(root, read_only=True)
            cold.append(request())
        warm = [request() for _ in range(warm_count)]
    return dict(
        fixture=fixture,
        cold=summary(cold),
        warm=summary(warm),
        response_sha256=digest,
        python=platform.python_version(),
        platform=platform.platform(),
        cold_definition="Fresh Library; OS page cache not flushed",
        queue_started=False,
        env_file_read=False,
        production_contacted=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/bestseller-v1-evidence/books-summary.json"))
    parser.add_argument("--cold", type=int, default=3)
    parser.add_argument("--warm", type=int, default=20)
    args = parser.parse_args()
    if min(args.cold, args.warm) < 1:
        parser.error("sample counts must be positive")
    output = args.output.resolve()
    if not output.is_relative_to(Path("/tmp").resolve()):
        parser.error("summary output must be under /tmp")
    with tempfile.TemporaryDirectory(prefix="books-http-benchmark-", dir="/tmp") as temporary:
        result = benchmark(Path(temporary) / "fixture-data", args.cold, args.warm)
    paths = [ENGINE / "tools/webui/library.py", ENGINE / "tools/webui/book_cache.py"]
    result["source_sha256"] = {
        str(p.relative_to(ENGINE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()
    }
    put(output, result)
    for name in ("cold", "warm"):
        row = result[name]
        print(
            f"{name} GET /api/books: n={row['count']} median={row['median_ms']:.3f}ms "
            f"p95={row['p95_ms']:.3f}ms max={row['max_ms']:.3f}ms"
        )
    print(f"summary={output}")


if __name__ == "__main__":
    main()
