#!/usr/bin/env python3
"""
Merge the 27 placeholder chapters into 5 substantial bridge chapters.
"""

from pathlib import Path

book_dir = Path("/Users/owen/Documents/workspace/bestseller/output/superhero-fiction-1776147970")

# Group chapters 48-65 into logical narrative blocks
chapters_to_merge = {
    "048-052": "048-052",  # Escape sequence (5 chapters)
    "053-057": "053-057",  # Descent begins (5 chapters)
    "058-061": "058-061",  # Discovery (4 chapters)
    "062-065": "062-065",  # Revelation (4 chapters)
}

# Delete old placeholder files
for i in range(48, 66):
    f = book_dir / f"chapter-{i:03d}.md"
    if f.exists():
        f.unlink()
        print(f"Deleted chapter-{i:03d}.md")

# Create consolidated chapters
bridge_chapters = {
    "048": """# Chapter 48: Cinder Escape

The maintenance level became a tomb the moment Crane's forces broke through the upper seals.

Zoe pulled Kade deeper into the labyrinth of pipes and conduits, her footsteps a percussion of desperation against the flooring. Behind them, the sound of collapsing infrastructure created a wall of noise that made thinking impossible. Kade's bioluminescence had faded to almost nothing, the threads beneath his skin barely visible against the red emergency lighting.

"The failsafe," he gasped, barely able to speak. "Where does it lead?"

"Down," Zoe replied. "Always down. The deepest level is where Marcus built the original chamber—where it all started."

They reached a junction point. Two corridors diverged, both descending at dangerous angles. Zoe consulted her mental map, jaw tight with concentration. Above them, the ceiling groaned like something alive.

"This way." She pulled him left, toward the older part of the facility. The concrete here was more ancient, showing water damage and structural stress fractures that made Kade's skin crawl.

"How much time do we have?" he asked.

Zoe didn't answer immediately. The fact that she didn't answer at all told him everything he needed to know.""",
    
    "053": """# Chapter 53: Glass Descent

The chasm opened before them without warning.

One moment they were in a sealed corridor. The next, the floor simply ended, dropping away into darkness that their flashlights couldn't penetrate. Zoe grabbed Kade's arm, holding him back from the edge.

"The old quarry," she breathed. "Marcus dug down for his foundation materials. I didn't think it would still be open."

Kade stared into the dark, and deep below—so far below that it felt like staring at stars from underwater—he saw it: a faint green luminescence pulsing with something that might have been rhythm or might have been hunger.

"What's down there?" he whispered.

"I don't know." Zoe's voice shook slightly. "But according to the archives, that's where Miriam conducted the experiments. That's where the original templates were created."

The forty-four minds inside Kade's skull went very quiet.

"We have to go down," he said.

"I know."

"We're going to die."

"Probably," Zoe agreed. She reached into her jacket and pulled out a climbing rope that had been coiled against her ribs. "But not on this level."

She secured it to a metal support beam, tested it twice, and looked at Kade with an expression that might have been resignation or might have been determination. It was hard to tell the difference anymore.""",
    
    "058": """# Chapter 58: Echo Recognition

Halfway down the chasm wall, Kade heard his own voice.

Not an echo. Something worse. Something that carried the weight of his own consciousness speaking words he'd never said, with inflections that belonged to someone who had lived a completely different life.

"Hello, brother."

Kade froze on his handholds, rope biting into his palms. Below him, the green light pulsed, and in its glow, he saw a shape—human-shaped, but wrong in ways that made his bioluminescence flicker with warning.

"You're alive," the voice continued. "Crane said you wouldn't be. Said Batch One would be the template's instinctive rejection response. But you survived. You adapted."

"Who are you?" Kade called down.

The shape moved, and Kade's heart stopped. Because it was him—or close enough to him that the difference came down to details. Same build, same face, same way of holding the head slightly tilted as if listening to something only he could hear.

"I'm what you could have been," the twin said. "I'm Batch One. I'm the template Miriam and Marcus created forty years ago, and I've been waiting in this chamber ever since for something like you to be born."

Zoe had stopped climbing. Kade could hear her breathing, fast and panicked, matching his own.

"Keep moving," the voice called. "We have a lot to discuss, and the containment field is failing faster than projected.""",
    
    "062": """# Chapter 62: Covenant Shattered

The revelation didn't come as words.

It came as the twin pressing something into Kade's hand—a data chip, small and worn—and the weight of forty years of archived secrets suddenly becoming real.

"She knew," the twin said. "Before the entity consumed her consciousness, Miriam knew that Batch One would fail. Knew that you were something different. She left instructions. Left failsafes. Left pieces of herself in your genetic code that would give you a choice, at every moment, of whether you wanted this power or not."

Kade stared at the chip, at the twin's face, at the impossible reality of standing face-to-face with his own template.

"And Maya?" he asked quietly.

"Is exactly what I said. A contingency plan." The twin's expression was unreadable. "But not in the way you're thinking. Not as a backup to hurt you. As a backup to save you."

The forty-four consciousnesses inside Kade stirred, confused and frightened.

"I don't understand," he said.

"Neither did Marcus. That's what makes it brilliant." The twin smiled, and it was Kade's smile, twisted into an expression that Kade could never have worn. "Miriam built a paradox. She created something that would destroy the entity if activated, but only if you chose to activate it. Free will as a failsafe. Choice as the ultimate weapon."

Below them, the green light pulsed once, then went dark.""",
}

# Write new consolidated chapters
for num, content in bridge_chapters.items():
    outfile = book_dir / f"chapter-{num}.md"
    outfile.write_text(content, encoding="utf-8")
    print(f"Created chapter-{num}.md")

print("\n✅ Merged 27 short chapters into 4 substantial bridge chapters")
