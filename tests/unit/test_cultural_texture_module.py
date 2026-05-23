from __future__ import annotations

from pydantic import ValidationError
import pytest

from bestseller.domain.calendar_system import CalendarSystem, Festival
from bestseller.domain.cultural_texture import (
    CulturalTextureModule,
    MaterialPaletteItem,
)
from bestseller.domain.honorific_system import HonorificSystem
from bestseller.domain.religious_organization import ReligiousOrganization
from bestseller.services.cultural_texture_density_gate import (
    cultural_module_mode_for_category,
    validate_religious_sacred_sites,
)

pytestmark = pytest.mark.unit


def _item(category: str, name: str) -> MaterialPaletteItem:
    return MaterialPaletteItem(
        category=category,  # type: ignore[arg-type]
        name=name,
        sensory_hook=f"{name}的余温",
        class_signal="庶民日常",
    )


def _module() -> CulturalTextureModule:
    return CulturalTextureModule(
        palette=[
            _item("food", "冷茶"),
            _item("food", "麦饼"),
            _item("clothing", "青布短褐"),
            _item("tool", "竹筹"),
            _item("ornament", "旧银簪"),
            _item("music", "巷口笛声"),
            _item("vehicle", "乌篷船"),
            _item("tool", "油纸伞"),
        ],
        daily_rituals=["晨起净手", "入门称名", "饭前让席"],
        taboo_behaviors=["直呼长辈名讳", "祭日前动刀"],
        aesthetic_zeitgeist="重清雅而避浮夸。",
    )


def test_palette_minimum_diversity() -> None:
    with pytest.raises(ValidationError):
        CulturalTextureModule(
            palette=[_item("food", f"食物{i}") for i in range(8)],
            daily_rituals=["晨起净手"],
            taboo_behaviors=["直呼长辈名讳"],
            aesthetic_zeitgeist="重礼。",
        )


def test_honorific_lookup() -> None:
    system = HonorificSystem(
        superior_to_inferior={"elder->disciple": "小徒"},
        inferior_to_superior={"disciple->elder": "师尊"},
        peer_address={"disciple->disciple": "师兄"},
        kinship_terms={"younger->older": "阿兄"},
        civil_to_military={"civil->general": "将军"},
        monastic_or_religious={"lay->monk": "法师"},
        forbidden_addresses=["老东西"],
    )
    assert system.lookup("disciple", "elder") == "师尊"
    assert system.lookup("disciple", "disciple") == "师兄"


def test_calendar_festival_intersection() -> None:
    calendar = CalendarSystem(
        calendar_type="lunar",
        major_festivals=[
            Festival(
                name="上元灯节",
                season="春",
                activities=["观灯", "猜谜"],
                symbolism="人群与隐秘线索交错",
                plot_hooks=["灯谜藏线索"],
            )
        ],
        seasonal_phases=["早春", "梅雨"],
        forbidden_dates=["国丧七日"],
    )
    assert calendar.festivals_for_plot_hook("灯谜藏线索")


def test_religious_hierarchy_consistency() -> None:
    org = ReligiousOrganization(
        name="白塔会",
        deities=["无名星"],
        core_doctrine="苦修换取预言碎片。",
        ritual_calendar=["上元灯节"],
        hierarchy=["会首", "长老", "执事", "信徒"],
        sacred_sites=["青崖", "云外"],
        conflict_with=["漕帮"],
        schism_history=None,
    )
    assert validate_religious_sacred_sites(org, region_names={"青崖"}) == ["云外"]


def test_module_applicable_categories() -> None:
    assert cultural_module_mode_for_category("历史架空") == "strict"
    assert cultural_module_mode_for_category("都市轻喜") == "warn"

