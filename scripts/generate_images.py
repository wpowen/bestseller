#!/usr/bin/env python3
"""
《天机录》配套图片批量生成脚本

Usage:
  python scripts/generate_images.py --provider minimax --category characters --limit 2
  python scripts/generate_images.py --provider openai --category all
  python scripts/generate_images.py --category scenes --force
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# ── 加载环境变量 ─────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from image_prompts import ALL_CATEGORIES, CATEGORY_DIRS

# ── 输出目录 ─────────────────────────────────────────────────
OUTPUT_BASE = ROOT / "output" / "天机录" / "images"

# ── MiniMax 配置 ─────────────────────────────────────────────
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"

# ── OpenAI 配置 ──────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# ─────────────────────────────────────────────────────────────
# MiniMax 图片生成
# ─────────────────────────────────────────────────────────────

async def generate_minimax(client: httpx.AsyncClient, prompt: str, size: str) -> bytes:
    """调用 MiniMax image-01 生成图片，返回图片二进制数据"""
    # MiniMax size mapping: 1024x1024 → "1:1", 1792x1024 → "16:9", 1024x1792 → "9:16"
    aspect_map = {
        "1024x1024": "1:1",
        "1792x1024": "16:9",
        "1024x1792": "9:16",
    }
    aspect_ratio = aspect_map.get(size, "1:1")

    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "image-01",
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "response_format": "url",
        "n": 1,
    }

    resp = await client.post(
        f"{MINIMAX_BASE_URL}/image_generation",
        headers=headers,
        json=payload,
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()

    # 检查 MiniMax 业务错误码（0 = 成功）
    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code", 0) != 0:
        raise ValueError(f"MiniMax API error {base_resp.get('status_code')}: {base_resp.get('status_msg')}")

    # 响应格式: {"data": {"image_urls": ["https://..."]}, ...}
    inner = data.get("data", {})
    if isinstance(inner, dict):
        urls = inner.get("image_urls") or inner.get("image_url") or []
        if isinstance(urls, str):
            urls = [urls]
        if urls:
            img_resp = await client.get(urls[0], timeout=60.0)
            img_resp.raise_for_status()
            return img_resp.content

    # 旧格式兼容：data 是 list
    if isinstance(inner, list) and inner:
        img_url = inner[0].get("url") or inner[0].get("image_url", "")
        if img_url.startswith("http"):
            img_resp = await client.get(img_url, timeout=60.0)
            img_resp.raise_for_status()
            return img_resp.content

    # 处理异步任务（返回 task_id）
    task_id = data.get("task_id") or (data.get("data") or {}).get("task_id")
    if task_id:
        return await poll_minimax_task(client, task_id, headers)

    raise ValueError(f"MiniMax unexpected response: {json.dumps(data, ensure_ascii=False)[:500]}")


async def poll_minimax_task(client: httpx.AsyncClient, task_id: str, headers: dict) -> bytes:
    """轮询 MiniMax 异步任务直到完成"""
    for attempt in range(30):  # 最多等待 5 分钟
        await asyncio.sleep(10)
        resp = await client.get(
            f"{MINIMAX_BASE_URL}/query/image_generation",
            params={"task_id": task_id},
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status") or data.get("task_status", "")

        if status in ("Success", "success", "completed", "SUCCEEDED"):
            file_info = (data.get("file_information") or [{}])[0]
            img_url = (
                file_info.get("download_url")
                or (data.get("data") or {}).get("url")
                or (data.get("images") or [{}])[0].get("url", "")
            )
            if img_url:
                img_resp = await client.get(img_url, timeout=60.0)
                img_resp.raise_for_status()
                return img_resp.content
            raise ValueError(f"Task succeeded but no URL: {data}")

        if status in ("Failed", "failed", "FAILED"):
            raise ValueError(f"MiniMax task failed: {data}")

        print(f"    ⏳ 等待任务完成... ({attempt + 1}/30, status={status})")

    raise TimeoutError(f"MiniMax task {task_id} timed out after 5 minutes")


# ─────────────────────────────────────────────────────────────
# OpenAI DALL-E 3 图片生成
# ─────────────────────────────────────────────────────────────

async def generate_openai(client: httpx.AsyncClient, prompt: str, size: str) -> bytes:
    """调用 OpenAI DALL-E 3 生成图片"""
    # DALL-E 3 支持的尺寸: 1024x1024, 1792x1024, 1024x1792
    valid_sizes = {"1024x1024", "1792x1024", "1024x1792"}
    if size not in valid_sizes:
        size = "1024x1024"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "dall-e-3",
        "prompt": prompt[:4000],  # DALL-E 3 prompt 限制
        "size": size,
        "quality": "hd",
        "n": 1,
    }

    resp = await client.post(
        "https://api.openai.com/v1/images/generations",
        headers=headers,
        json=payload,
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()

    img_url = data["data"][0]["url"]
    img_resp = await client.get(img_url, timeout=60.0)
    img_resp.raise_for_status()
    return img_resp.content


# ─────────────────────────────────────────────────────────────
# 主生成逻辑
# ─────────────────────────────────────────────────────────────

async def generate_one(
    client: httpx.AsyncClient,
    provider: str,
    item: dict,
    category: str,
    force: bool,
) -> dict:
    """生成单张图片并保存到对应目录"""
    out_dir = OUTPUT_BASE / CATEGORY_DIRS[category]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / item["filename"]

    if out_path.exists() and not force:
        print(f"  ⏭  跳过（已存在）: {item['name']}")
        return {"id": item["id"], "name": item["name"], "path": str(out_path), "status": "skipped"}

    print(f"  🎨 生成中: {item['name']} [{provider}] ...", flush=True)
    t0 = time.time()

    try:
        if provider == "minimax":
            img_data = await generate_minimax(client, item["prompt"], item.get("size", "1024x1024"))
        elif provider == "openai":
            img_data = await generate_openai(client, item["prompt"], item.get("size", "1024x1024"))
        else:
            raise ValueError(f"Unknown provider: {provider}")

        out_path.write_bytes(img_data)
        elapsed = time.time() - t0
        size_kb = len(img_data) // 1024
        print(f"  ✅ 完成: {item['name']} ({size_kb}KB, {elapsed:.1f}s)")
        return {"id": item["id"], "name": item["name"], "path": str(out_path), "status": "ok"}

    except httpx.HTTPStatusError as e:
        print(f"  ❌ HTTP错误 {e.response.status_code}: {item['name']}")
        print(f"     响应: {e.response.text[:300]}")
        return {"id": item["id"], "name": item["name"], "status": "error", "error": str(e)}

    except Exception as e:
        print(f"  ❌ 错误: {item['name']} — {e}")
        return {"id": item["id"], "name": item["name"], "status": "error", "error": str(e)}


async def run(provider: str, categories: list[str], limit: int | None, force: bool) -> None:
    """批量生成所有图片"""
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

    if not MINIMAX_API_KEY and provider == "minimax":
        print("❌ 缺少 MINIMAX_API_KEY，请在 .env 文件中配置")
        return
    if not OPENAI_API_KEY and provider == "openai":
        print("❌ 缺少 OPENAI_API_KEY，请在 .env 文件中配置")
        return

    results: list[dict] = []
    total = 0

    async with httpx.AsyncClient() as client:
        for category in categories:
            items = ALL_CATEGORIES.get(category, [])
            if limit:
                items = items[:limit]

            if not items:
                print(f"⚠️  类别 '{category}' 没有找到任何条目")
                continue

            print(f"\n📂 类别: {category}（{len(items)} 张）")
            print("─" * 50)

            for item in items:
                result = await generate_one(client, provider, item, category, force)
                result["category"] = category
                results.append(result)
                total += 1

                # 限速：避免 API 频率限制
                if result["status"] == "ok":
                    await asyncio.sleep(1.5)

    # 保存索引文件
    index_path = OUTPUT_BASE / "images_index.json"
    index = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "provider": provider,
        "total": total,
        "results": results,
    }
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors = sum(1 for r in results if r["status"] == "error")

    print(f"\n{'═'*50}")
    print(f"✅ 完成: {ok}  ⏭ 跳过: {skipped}  ❌ 错误: {errors}  共: {total}")
    print(f"📄 索引文件: {index_path}")


# ─────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="《天机录》配套图片生成脚本")
    parser.add_argument(
        "--provider",
        choices=["minimax", "openai"],
        default="minimax",
        help="图片生成 API 提供商（默认: minimax）",
    )
    parser.add_argument(
        "--category",
        default="all",
        help="生成类别: characters | scenes | key_scenes | artifacts | covers | all（默认: all）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="每个类别最多生成几张（用于测试）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新生成已有图片",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有计划生成的图片，不实际生成",
    )

    args = parser.parse_args()

    if args.list:
        total = 0
        for cat, items in ALL_CATEGORIES.items():
            print(f"\n{cat} ({len(items)} 张):")
            for item in items:
                print(f"  {item['id']:30s} → {item['filename']}")
                total += 1
        print(f"\n共计 {total} 张")
        return

    if args.category == "all":
        categories = list(ALL_CATEGORIES.keys())
    else:
        categories = [c.strip() for c in args.category.split(",")]
        for cat in categories:
            if cat not in ALL_CATEGORIES:
                print(f"❌ 未知类别: {cat}")
                print(f"   可用类别: {', '.join(ALL_CATEGORIES.keys())}")
                sys.exit(1)

    print(f"🖼  《天机录》图片生成")
    print(f"   提供商: {args.provider}")
    print(f"   类别: {args.category}")
    if args.limit:
        print(f"   限制: 每类别前 {args.limit} 张")
    print(f"   输出目录: {OUTPUT_BASE}")
    print()

    asyncio.run(run(args.provider, categories, args.limit, args.force))


if __name__ == "__main__":
    main()
