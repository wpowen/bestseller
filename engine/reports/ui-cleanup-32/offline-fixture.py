"""Isolated offline browser fixture: real HTTP/files, model and process creation disabled."""

import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from unittest.mock import patch

repo = Path.cwd()
sys.path[:0] = [str(repo / "engine/tools/webui"), str(repo / "engine")]
import jobs
import server
from library import save, read, now

root = Path(tempfile.mkdtemp(prefix="ui32-browser-"))
(root / "env").write_text("MINIMAX_API_KEY=offline-fixture-not-a-secret\n")
Path("/tmp/ui32-browser-root").write_text(str(root))


def fixture_book(name, status="complete", polish=False):
    folder = root / "books/webui" / name
    save(folder / "book.yaml", dict(chapters=2, words_per_chapter=2300))
    save(
        folder / "web.json",
        dict(
            title={"complete": "渡口归人", "paused": "未寄出的信", "running": "正在写作的书"}[status],
            genre="cozy-mystery",
            status=status,
            chapters=2,
            polish=polish,
            created="2026-11-05T08:00:00+08:00",
            reason="独立去AI处理完成；正文与原检查点保留",
        ),
    )
    save(
        folder / "run.json",
        dict(
            status="complete" if status == "complete" else "running",
            started="2026-11-05T08:01:00+08:00",
            finished="2026-11-05T08:15:00+08:00",
            book=dict(chapters=2, prompt_pack="cozy-mystery"),
        ),
    )
    save(
        folder / "story_bible.yaml",
        dict(
            title="渡口归人",
            premise="退潮以后，摆渡人听见失踪多年的妹妹在船底敲门。",
            characters=[dict(name="许安", role="protagonist", desire="找回妹妹", voice="话少，心软")],
            world=dict(rules=["每次渡河要留下一段记忆。"]),
        ),
    )
    save(
        folder / "writing_outlines/001.json",
        dict(chapter=1, title="退潮", conflict="许安不肯烧船，父亲只想他活着。", hook="船底响起妹妹的声音。"),
    )
    save(folder / "calls.json", [dict(usage=None, input_bytes=100, output_token_cap=100)] * 53)
    for n in range(1, 3):
        save(folder / f"chapter-{n:03d}.body.md", "许安推开门，父亲站在水里。\n\n“别再等了。”父亲说。\n\n他没有答应。")
        save(
            folder / f"chapters/{n:03d}.json",
            dict(title=["退潮", "归人"][n - 1], status="complete", checks=dict(within_band=True, hard_conflicts=[])),
        )
        save(
            folder / f"final/chapter-{n:03d}.md",
            "许安推开门，父亲站在水里。\n\n“别再等了。”父亲说。\n\n船底忽然响了三声。他认得那个节奏。",
        )
        save(folder / f"final/chapter-{n:03d}.deai.json", dict(status="polished", complete=True, after_score=8))
    return folder


fixture_book("finished")
fixture_book("paused", "paused")
fixture_book("blocked-delete", "running")


def fake_topic(self, data):
    from job_inputs import whole

    count = whole(data.get("chapters", 50), 20, 2000, "章数")
    key = str(uuid.uuid4())
    card = dict(
        title="渡口借命，归来的人少了一岁",
        hook="摆渡人用余生换回失踪乘客",
        tags=["民俗", "解谜", "逆袭"],
        premise="摆渡人要在涨潮前找回失踪乘客。",
        golden_finger_rule="每渡一人可以借回一天寿命",
        opening="退潮后船底传来敲门声。",
        engine="找回乘客、寻找失船、揭开渡口身世",
        scores={"不可补全": 8, "点击欲": 8, "长篇引擎": 7, "好懂": 9},
        axes=dict(shell="民俗悬疑", variable="渡船", desire="守护", emotion="解谜"),
    )
    save(
        self.library.own / ".topics" / key / "topic.json",
        dict(
            id=key,
            status="complete",
            genre=data["genre"],
            chapters=count,
            idea=data.get("idea", ""),
            channel=data.get("channel", ""),
            candidates=[card],
        ),
    )
    return self.get_topic(key)


def offline_launch(self, folder, data, kind="book"):
    data.update(status="paused", reason="离线验收：写作进程未启动")
    save(folder / "web.json", data)


sys.argv = ["webui", "--env", str(root / "env"), "--books", str(root / "books"), "--port", "18795"]
with (
    patch.object(jobs.Queue, "topic", fake_topic),
    patch.object(jobs.Queue, "launch", offline_launch),
    patch("subprocess.Popen", side_effect=AssertionError("offline fixture prohibits subprocesses")),
):
    server.main()
