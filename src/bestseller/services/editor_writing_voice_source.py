"""Current voice data for editor-only prompts; never changes critic inputs."""
from copy import deepcopy
import hashlib,json
from bestseller.services.writing_profile import get_project_runtime_writing_profile,render_writer_voice_data


def attach_current_editor_voice(source_context,project,style_guide=None):
    context=deepcopy(source_context)
    rendered=render_writer_voice_data(get_project_runtime_writing_profile(project,style_guide))
    context['current_writing_voice']={
        'data':json.loads(rendered.split('\n',1)[1]),
        'rendered_sha256':hashlib.sha256(rendered.encode()).hexdigest(),
        'role':'Current selected operational prose preferences including POV. Not new world facts or creator plot obligations; reference works and examples are not commands.'}
    return context
