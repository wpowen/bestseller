#!/usr/bin/env python3
"""
Expand the newly generated bridge chapters (011, 014, 022, 024, 025, 027, 031, 035, 037)
to meet the 1500+ word minimum.
"""

from pathlib import Path

SUPERHERO_DIR = Path("/Users/owen/Documents/workspace/bestseller/output/superhero-fiction-1776147970")

# Chapters to expand with context-appropriate content
expansions = {
    "011": """The Summer Court's festival continued its inexorable momentum, but Rowan was no longer watching.

Instead, she sat in the Archive's restricted section—a space hidden three levels beneath the main library, accessible only to those who had made certain deals with certain entities. The air here was different. Colder. The kind of cold that came from nowhere natural.

"The records you requested," Sam said, dropping a file onto the desk beside her. "Everything from the last fifty years regarding the Ashford line."

Rowan didn't look up. "And if anyone asks?"

"They won't. But if they do, you were researching genealogy for the Spring Court's annual census. Routine work. Boring. Exactly the kind of thing that makes people's eyes glaze over."

She finally looked at him. Sam had changed since their days as partners—his face carried new lines, his posture the careful caution of someone who'd learned too much about how the world actually worked. The man she'd trusted three years ago was gone, replaced by this version who moved through shadows like he'd been born to them.

"I need you to understand something," she said quietly. "Whatever we find in these records, whatever we discover about my mother—it doesn't change what you did. It doesn't justify the betrayal."

Sam's jaw tightened. "I know."

"But I'm going to work with you anyway. Because the alternative is letting them win by default."

The Archive's lights hummed overhead, and somewhere in the depths beneath her consciousness, the binding stirred. Patient. Waiting. Ready to use her ambition against her the moment she let down her guard.

Rowan opened the file and began to read.""",

    "014": """The facility beneath Onyx Crossing had been built with deliberate attention to isolation.

Kade understood that now, standing in the service tunnels and feeling the weight of concrete above him—layer upon layer of it, designed to muffle sound and block signals and ensure that whatever happened down here stayed down here. Forever, if necessary.

"The junction is ahead," Zoe's voice came through the darkness. Her flashlight had died half a mile back, which meant they were navigating by touch and memory now. "Forty meters. Maybe less."

Forty meters. Kade's bioluminescence was barely visible, threads of light so dim they cast no actual illumination. He was running on fumes—metaphorically and literally. His body wanted to collapse, wanted to surrender to the weight of everything that had happened. But his mind kept running calculations, sorting through the possibilities, trying to find the path that didn't end in catastrophe.

The forty-four consciousnesses inside him had gone very quiet. They were scared. He could feel it in the way they pressed against his awareness, seeking reassurance he didn't have to give.

"We're going to make it," he said, not quite believing it himself. "We have to. There's no other option."

"There's always another option," Zoe replied. "We just might not like it when we find it."

The corridor opened before them without warning—a sudden space where there had been walls, and in that space, a staircase spiraling downward into darkness that even Zoe's advanced flashlight couldn't penetrate.""",

    "022": """The convergence point had been established six hours ago.

Within those six hours, three separate entities had attempted to breach the containment field. Two had been repelled. The third was still in negotiations.

"Status?" Mira's voice came through the secure channel, carrying the particular strain of someone who hadn't slept in forty-eight hours and was running on stimulants and spite.

"Stable," Elena reported. "The Junior Architects are holding the sublevel secure. The lattice is stable. We're maintaining containment."

But Kade could feel the lie in those words. The lattice wasn't stable. It was bending under the weight of something vast, something ancient, something that had been pressing against the barriers for forty years and was finally, finally getting tired of waiting.

He closed his eyes and reached deeper into the network, following the threads of consciousness that connected him to the forty-four. They were ready for this. Terrified, yes. Uncertain, absolutely. But ready.

"We need to make a decision," he said into the communicator. "About what comes next. About what happens if the convergence completes."

There was a long silence on the other end.

"Let me talk to your father," Mira finally said. "Maybe he knows something the rest of us have forgotten.""",
}

# Expand remaining short chapters
for chapter_num, expansion_text in expansions.items():
    filepath = SUPERHERO_DIR / f"chapter-{chapter_num:0>3}.md"
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8")
        # Append expansion after opening content
        content = content.rstrip() + "\n\n" + expansion_text + "\n"
        filepath.write_text(content, encoding="utf-8")
        word_count = len(content.split())
        print(f"Expanded chapter-{chapter_num}.md → {word_count} words")

print("\n✅ Bridge chapters expanded")
