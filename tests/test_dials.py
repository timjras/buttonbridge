from renderer import dials


def light(state="on", brightness=128, modes=("brightness",)):
    return {"state": state, "attributes": {"brightness": brightness if state == "on" else None, "supported_color_modes": list(modes)}}


def test_light_brightness():
    reading = dials.read("light.desk", light())
    assert reading.value == 50 and reading.text == "50%" and reading.level() == 50 and reading.on
    assert reading.after(50, 3) == 65
    assert reading.after(50, -20) == 0
    off = dials.read("light.desk", light("off"))
    assert off.value == 0 and off.text == "Off" and not off.on
    assert dials.call_for("light.desk", 0) == ("light", "turn_off", {})
    assert dials.call_for("light.desk", 65) == ("light", "turn_on", {"brightness_pct": 65})


def test_on_off_light_turns_all_the_way():
    reading = dials.read("light.porch", light("off", modes=("onoff",)))
    assert reading.after(0, 1) == 100


def test_volume_and_thermostat():
    speaker = dials.read("media_player.office", {"state": "playing", "attributes": {"volume_level": 0.3}})
    assert speaker.text == "30%" and speaker.after(30, -1) == 28
    assert dials.call_for("media_player.office", 28) == ("media_player", "volume_set", {"volume_level": 0.28})
    heat = dials.read("climate.hall", {"state": "heat", "attributes": {"temperature": 20.5, "min_temp": 7, "max_temp": 30}})
    assert heat.text == "20.5°" and heat.after(20.5, 2) == 21.5 and heat.after(29.5, 5) == 30


def test_a_sensor_just_shows_its_state():
    reading = dials.read("sensor.outside", {"state": "12.4", "attributes": {"unit_of_measurement": "°C"}})
    assert reading.value is None and reading.text == "12.4 °C" and reading.level() is None


def test_default_press():
    assert dials.default_press("media_player.office") == "media_player.media_play_pause"
    assert dials.default_press("light.desk") == "toggle"
    assert dials.default_press("input_number.volume") == "none"
