from pathlib import Path

import pytest

from renderer.config import ConfigError, GoTo, load_config, parse_config, unused_pages

EXAMPLE = Path(__file__).parent.parent / "examples" / "streamdeck.yaml"

# A minimal valid panel, for tests that only care about its pages.
PANEL = """
panels:
  hall:
    id: sd-000001
    size: mini
    pages:
"""


def problems(text: str) -> list[str]:
    with pytest.raises(ConfigError) as err:
        parse_config(text)
    return err.value.problems


def test_example_config_is_valid():
    config = load_config(EXAMPLE)
    assert set(config.panels) == {"hallway", "kitchen"}
    hallway = config.panels["hallway"]
    assert hallway.key_count == 6 and config.panels["kitchen"].key_count == 15
    assert hallway.pages["home"].buttons[3].tap == GoTo(go_to="lights")
    assert config.panels["kitchen"].pages["home"].buttons[2].lights.floor == "ground_floor"
    assert unused_pages(config) == []


def test_bare_on_off_states_are_text():
    config = parse_config(
        PANEL
        + """
      home:
        buttons:
          - key: 1
            entity: light.hall
            when:
              on: { background: amber }
              off: { background: black }
"""
    )
    assert set(config.panels["hall"].pages["home"].buttons[0].when) == {"on", "off"}


def test_misspelt_setting_names_the_line():
    found = problems(PANEL + "      home:\n        buttons:\n          - key: 1\n            entitty: light.hall\n")
    assert found == ["line 10: 'entitty' isn't a setting here. Check the spelling."]


def test_bad_panel_id():
    found = problems("panels:\n  hall:\n    id: 3c71bf\n    size: mini\n    pages: { home: {} }\n")
    assert found == ["line 3: id should look like sd-3c71bf, the ID a new panel shows on its keys."]


def test_bad_size():
    found = problems("panels:\n  hall:\n    id: sd-000001\n    size: module12\n    pages: { home: {} }\n")
    assert found == ["line 4: size should be one of: mini, module6, module15, module32, plus."]


def test_key_beyond_panel_size():
    found = problems(PANEL + "      home:\n        buttons:\n          - key: 7\n")
    assert found == ["line 9: Key 7 doesn't exist: a mini panel has 6 keys."]


def test_duplicate_section_names():
    found = problems(PANEL + "      home: {}\n      home: {}\n")
    assert found == ["line 8: 'home' appears twice in the same section."]


def test_go_to_must_be_a_page_on_the_same_panel():
    found = problems(PANEL + "      home:\n        buttons:\n          - key: 1\n            tap: { go_to: nowhere }\n")
    assert found == ["line 10: Panel 'hall' has no page called 'nowhere'."]


def test_start_page_must_exist():
    found = problems(PANEL + "      main: {}\n")
    assert found == ["line 4: Panel 'hall' has no page called 'home'."]


def test_same_id_twice():
    found = problems(
        """
panels:
  a: { id: sd-000001, size: mini, pages: { home: {} } }
  b: { id: sd-000001, size: mini, pages: { home: {} } }
"""
    )
    assert any("same id" in p for p in found)


def test_key_used_twice():
    found = problems(PANEL + "      home:\n        buttons:\n          - key: 2\n          - key: 2\n")
    assert found == ["line 10: Key 2 is used twice on page 'home'."]


def test_lights_needs_exactly_one_place():
    found = problems(PANEL + "      home:\n        buttons:\n          - key: 1\n            lights: { area: kitchen, floor: ground }\n")
    assert len(found) == 1 and found[0].startswith("line 10: ") and "area: or floor:" in found[0]


def test_lights_states_are_any_on_and_all_off():
    found = problems(
        PANEL + "      home:\n        buttons:\n          - key: 1\n            lights: { area: kitchen }\n"
        "            when: { on: { background: amber } }\n"
    )
    assert found == ["line 11: For lights:, the states are any_on and all_off."]


def test_toggle_needs_something_to_act_on():
    found = problems(PANEL + "      home:\n        buttons:\n          - key: 1\n            tap: turn_off\n")
    assert found == ["line 10: turn_off needs an entity: or lights: to act on."]


def test_wrong_action_gives_one_plain_message():
    found = problems(PANEL + "      home:\n        buttons:\n          - key: 1\n            tap: togle\n")
    assert len(found) == 1 and found[0].startswith("line 10: tap should be toggle, turn_on")


def test_unknown_style_and_image_type():
    found = problems(PANEL + "      home:\n        image: { type: sundial }\n")
    assert len(found) == 1 and found[0].startswith("line 8: image needs a type")
    found = problems("styles: {}\n" + PANEL + "      home:\n        buttons:\n          - { key: 1, style: fancy }\n")
    assert found == ["line 10: There's no style called 'fancy' under 'styles:'."]


def test_bad_colour_and_clock_font():
    found = problems(PANEL + "      home:\n        image: { type: clock, font: comic }\n        buttons:\n          - { key: 1, background: pink }\n")
    assert sorted(found) == ["line 10: background should be a colour name (black, grey, dark, white, amber, blue, green, red, purple) or #rrggbb.", "line 8: font should be one of: montserrat, archivo, roboto, oswald, bebas."]


def test_unused_page_is_reported():
    config = parse_config(PANEL + "      home: {}\n      lost: {}\n")
    assert unused_pages(config) == ["hall/lost"]


def test_brightness_schedule_and_sleep():
    config = load_config(EXAMPLE)
    hallway = config.panels["hallway"]
    assert [s.at for s in hallway.brightness.schedule] == ["07:00", "sunset", "22:30"]
    assert hallway.sleep.page == "clock" and hallway.pages["clock"].brightness == 30
    assert config.panels["kitchen"].brightness.follow == "input_number.kitchen_panel_brightness"


def test_bad_brightness_settings():
    text = PANEL.replace("    pages:\n", "    brightness: { schedule: [{ at: '25:00', level: 50 }] }\n    sleep: { after_minutes: 5, page: dream }\n    pages:\n")
    found = problems(text + "      home: {}\n")
    assert found == ["line 6: at should be a time like 07:30, or sunrise or sunset."]
    text = PANEL.replace("    pages:\n", "    sleep: { after_minutes: 5, page: dream }\n    pages:\n")
    assert problems(text + "      home: {}\n") == ["line 6: Panel 'hall' has no page called 'dream'."]


def test_sleep_needs_a_trigger():
    text = PANEL.replace("    pages:\n", "    sleep: { between: ['22:00', '07:00'] }\n    pages:\n")
    assert parse_config(text + "      home: {}\n").panels["hall"].sleep.after_minutes is None
    text = PANEL.replace("    pages:\n", "    sleep: { page: home }\n    pages:\n")
    found = problems(text + "      home: {}\n")
    assert len(found) == 1 and "after_minutes:, between:, or both" in found[0]


def test_awake_seconds():
    text = PANEL.replace("    pages:\n", "    sleep: { between: ['22:00', '07:00'] }\n    pages:\n")
    assert parse_config(text + "      home: {}\n").panels["hall"].sleep.awake_seconds == 60
    text = PANEL.replace("    pages:\n", "    sleep: { between: ['22:00', '07:00'], awake_seconds: 2 }\n    pages:\n")
    assert len(problems(text + "      home: {}\n")) == 1


PLUS = """
panels:
  desk:
    id: sd-000002
    size: plus
    pages:
"""


def test_plus_dials():
    config = parse_config(
        PLUS
        + """
      home:
        dials:
          - dial: 1
            entity: light.desk
            step: 10
          - dial: 4
            entity: media_player.office
            press: { go_to: music }
      music: {}
"""
    )
    panel = config.panels["desk"]
    assert panel.key_count == 8 and panel.dial_count == 4
    assert panel.pages["home"].dials[0].step == 10
    assert unused_pages(config) == []  # dial 4 leads to music


def test_dial_problems():
    found = problems(
        PLUS
        + """
      home:
        dials:
          - dial: 5
          - dial: 2
            press: toggle
          - dial: 2
"""
    )
    assert found == [
        "line 10: Dial 5 doesn't exist: a plus panel has 4 dials.",
        "line 12: toggle needs an entity: to act on.",
        "line 13: Dial 2 is used twice on page 'home'.",
    ]


def test_dials_need_a_plus():
    found = problems(PANEL + "      home:\n        dials:\n          - dial: 1\n")
    assert found == ["line 9: A mini panel has no dials; only a Stream Deck + (size: plus) does."]
