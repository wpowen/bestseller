from __future__ import annotations

from bestseller.domain.crowd_dynamics import CrowdScene


def render_crowd_scene_prompt_block(scene: CrowdScene | dict | None) -> str:
    if scene is None:
        return ""
    if isinstance(scene, dict):
        scene = CrowdScene.model_validate(scene)
    lines = [
        "### Crowd Dynamics",
        f"- Size: {scene.crowd_size_class}",
        f"- Trigger: {scene.triggering_event}",
        f"- Mood arc: {' -> '.join(scene.mood_arc)}",
        f"- Resolution: {scene.resolution}",
    ]
    if scene.rumor_seed:
        lines.append(f"- Rumor seed: {scene.rumor_seed}")
    if scene.factional_split:
        lines.append(f"- Factional split: {', '.join(scene.factional_split[:4])}")
    return "\n".join(lines)

