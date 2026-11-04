"""One overall plan, lazy 25-chapter volumes, ten-chapter outline windows."""
from .longutil import ask
from .quality import require
from .text import pack
import re


def overall(client, book):
    count = (book.chapters + 24) // 25
    schema = ('{"title":"","premise":"","ending":"","canon":{"characters":'
              '[{"name":"","wants":"","voice":"","ability":"","limit":""}],'
              '"world_rules":[],"key_items":[]},"volumes":[{"number":1,"start":1,"end":25,'
              '"goal":"","price":"","payoff":""}]}').replace('"end":25', '"end":' + str(min(25, book.chapters)))

    def normalize(value):
        value = dict(value, volumes=value.get('volumes', value.get('canon', {}).get('volumes')))
        if book.chapters < 25 and isinstance(value['volumes'], list) and len(value['volumes']) == 1:
            volume = value['volumes'][0]
            if volume.get('end') == 25:
                value['volumes'] = [dict(volume, end=book.chapters)]
        return value

    def valid(value):
        require(isinstance(value.get('title'), str) and value['title'].strip())
        require(isinstance(value.get('premise'), str) and bool(value['premise'].strip()))
        require(isinstance(value.get('canon'), dict) and bool(value['canon'].get('characters')))
        require(isinstance(value.get('volumes'), list) and len(value['volumes']) == count)
        for idx, volume in enumerate(value['volumes'], 1):
            require(isinstance(volume, dict))
            require(volume.get('number') == idx and volume.get('start') == 25 * (idx - 1) + 1
                    and volume.get('end') == min(idx * 25, book.chapters))

    return ask(client, 'plan-overall', f'为{book.chapters}章、题材{book.prompt_pack}的小说写唯一全书总纲。'
               f'每卷最多25章，共{count}卷，最后一卷end必须为{book.chapters}；'
               '这里只列各卷走向，不写章纲。固定4—6名核心人物、'
               '他们的姓名/说话方式/欲望/能力上限，最多8件关键物品、6条世界规则。'
               '遵从用户题材、读者承诺与现实规则；主角成长有具体代价，禁止临时新增设定解局。'
               '每卷兑现前卷伏笔，最终10%章节只收不开；可以完全收束。'
               '若留续作钩子，至多2个且须在90%之前出现，严禁结尾空降新箱子、新人物或新秘密。'
               '不要把所有题材写成核账、协议和举证争执；事件靠行动和人物选择推进。'
               '设定简洁可供长期复用，总输出2200汉字内。',
               [], schema, cap=7000, validate=valid, max_attempts=2,
               normalize=normalize)


def chapter_window(client, plan, start, total, state, rolling, threads=None):
    end = min(start + 9, total)
    volumes = []
    for skeleton in plan['volumes']:
        if skeleton['end'] < start or skeleton['start'] > end:
            continue
        number = skeleton['number']
        volume = ask(client, f'plan-volume-{number}',
                     '现在才展开将要使用的这一卷，只写卷级规划，不列逐章大纲。'
                     '给3—5个阶段、伏笔开收和卷末兑现，各人物不得改名，能力遵守既定边界。'
                     '总输出900汉字内，open_threads/close_threads各至多5条。'
                     'close_threads必须写本卷兑现的已有伏笔编号，不得只写抽象的“收束主线”。'
                     '优先收最老的伏笔，同一疑问不得改名重复开。活跃目标≤6，硬上限12。'
                     '最后10%章节只收不开，末尾至多留2条续作钩子。',
                     [('本卷目标', pack(skeleton)), ('当前状态', pack(state)[:4500]),
                      ('已有伏笔编号与原义', pack(threads or {})),
                      ('全书总纲', pack(plan)[:6500]), ('滚动记忆', rolling[:900])],
                     '{"number":1,"goal":"","phases":[],"open_threads":[],"close_threads":[],"ending":""}',
                     cap=4500, max_attempts=2,
                     validate=lambda v: require(isinstance(v.get('phases'), list) and bool(v['phases'])))
        if threads and not volume.get('close_threads'):
            volume['close_threads'] = list(state.get('foreshadows', {}))[:3]
        volumes.append(dict(volume, start=skeleton['start'], end=skeleton['end']))

    def valid(value):
        chapters = value.get('chapters')
        require(isinstance(chapters, list) and len(chapters) == end - start + 1)
        require(all(isinstance(x, dict) for x in chapters))
        require([x.get('number') for x in chapters] == list(range(start, end + 1)))
        require(all(x.get('title') and isinstance(x.get('scenes'), list) and x['scenes'] for x in chapters))

    window = ask(client, f'plan-chapters-{start}-{end}',
                 f'只写第{start}—{end}章细纲，不生成其它章。每章2200汉字，两场戏；'
                 '每场给地点、人物、选择、后果，场景描述40—65汉字。'
                 '前后章承接已发生状态，人物改换场景时明确入场/离场与关键物品移动。'
                 '每章标明day/minute（故事第0天开始），跨日递增；回忆不得冒充当前时间。'
                 '每章开/收伏笔最多各1个，已有用固定编号，新开用一句短名；同一疑问不重复开。'
                 '回收必须回答原疑问或兑现承诺，初步线索、倒计时和计划推进不能冒充回收。'
                 '伏笔目标≤6、硬上限12，先收老坑再开新坑；本卷要收的条目须分配到具体章节。'
                 f'第{(total*9+9)//10}章起为收束期：不得新开；将所有活跃伏笔分配兑现，结尾最多留2条。'
                 '人物姓名只用固定名，无昵称别称。不要提前结束整本书。',
                 [('当前卷纲', pack(volumes)), ('当前状态', pack(state)[:4500]),
                  ('已有伏笔编号与原义', pack(threads or {})),
                  ('固定人物设定', pack(plan['canon'])), ('滚动记忆', rolling[:900])],
                 '{"chapters":[{"number":1,"title":"","day":0,"minute":540,'
                 '"scenes":[{"where":"","characters":[],"action":"","choice":"","consequence":""}],'
                 '"open_threads":[],"close_threads":[],"ending_hook":""}]}',
                 cap=8500, max_attempts=2, validate=valid)
    chapters = []
    for chapter in window['chapters']:
        # IDs are assigned only on a grounded opening, never reserved by a plan.
        openings = [re.sub(r'^F\d+\s*[:：]\s*', '', s) if isinstance(s,str) else s
                    for s in chapter.get('open_threads', [])]
        chapters.append(dict(chapter, open_threads=openings))
    return chapters, volumes
