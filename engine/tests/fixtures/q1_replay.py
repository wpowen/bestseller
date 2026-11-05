"""Offline three-chapter Q1 replay with fixed HTTP replies and clock."""

import datetime as dt
import io
import json
from pathlib import Path
import re
import shutil
import sys
from unittest.mock import patch
import yaml
from bookengine import q1, q1_plan
from bookengine.client import Client
from bookengine.storage import save

root = Path("/tmp/bestseller-v1-evidence/replay-work")
if root.exists():
    shutil.rmtree(root)
out = root / "books" / "sample"
out.mkdir(parents=True)
book = dict(prompt_pack="suspense-mystery", chapters=3, words_per_chapter=200, user_input="离线三章回归")
save(root / "book.yaml", yaml.safe_dump(book, allow_unicode=True))
bible = q1_plan.defaults(
    dict(
        title="归途",
        characters=[
            dict(name=n, role=role, voice="短句", desire="回家")
            for n, role in [("林宁", "protagonist"), ("阿宁", "support"), ("小周", "support")]
        ],
        volumes=[dict(number=1, start=1, end=3)],
    ),
    q1_plan.DATA["schemas"]["prepare"]["bible"],
)
bible["canon"] = dict(q1_plan.GROWTH["packs"]["suspense-mystery"])
bible["canon"]["hook_types"] = list(q1_plan.DATA["hook_types"])
save(out / "story_bible.yaml", yaml.safe_dump(bible, allow_unicode=True))
save(out / "threads.yaml", yaml.safe_dump(dict(threads=[])))
real_request = Client.request


def request(client, stage, prompt, cap, effort, attempt):
    if "volume" in stage:
        reply = dict(number=1, start=1, end=3)
    elif "chapters-" in stage:
        reply = dict(
            chapters=[
                dict(number=n, title=f"归途{n}", scenes=[dict(place="家", characters=["林宁"])]) for n in range(1, 4)
            ]
        )
    elif stage.endswith("-draft"):
        n = int(re.search(r"ch(\d+)", stage)[1])
        reply = "“" + ("我陪你回家" * 12) + "。”\n\n" + ("她推开门" * 35) + f"。第{n}日，他们到了家。"
    elif stage.endswith(("-finish", "-final-state")):
        reply = dict(
            type="日常",
            hook_type=q1_plan.DATA["hook_types"][0],
            payoff=dict(type="无兑现", fulfilled=False, quote=""),
            state={},
            edits=[],
            summary="他们到了家。",
            opens_new_mainline=False,
        )
    else:
        raise AssertionError("Unexpected request: " + stage)
    body = json.dumps(
        dict(
            model="MiniMax-M3.1-Flash",
            usage=dict(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            choices=[
                dict(
                    finish_reason="stop",
                    message=dict(content=reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)),
                )
            ],
        ),
        ensure_ascii=False,
    ).encode()
    with patch("urllib.request.urlopen", return_value=io.BytesIO(body)):
        return real_request(client, stage, prompt, cap, effort, attempt)


class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 5, tzinfo=dt.timezone.utc)


with (
    patch("bookengine.client.credentials", return_value=("https://offline.invalid", "offline-fixture")),
    patch.object(Client, "request", request),
    patch("datetime.datetime", FixedDateTime),
    patch("time.monotonic", return_value=1000.0),
    patch("time.time", return_value=1793836800.0),
    patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")),
):
    result = q1.run_q1(root / "book.yaml", out, root / "unused.env", Path.cwd().parent / "config")
assert result["status"] == "complete" and result["completed_chapters"] == 3, result
snapshot = Path(sys.argv[1])
snapshot.mkdir(parents=True, exist_ok=True)
count = 0
for p in sorted(out.rglob("*")):
    if p.is_file() and p.suffix in (".json", ".md", ".yaml"):
        target = snapshot / p.relative_to(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.read_bytes())
        count += 1
stages = [r["stage"] for r in json.loads((out / "calls.json").read_text())]
print(json.dumps(dict(files=count, stages=stages), ensure_ascii=False))
