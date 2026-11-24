# How panels are configured

## In one paragraph

Each panel is set up from a **setup page in the Home Assistant sidebar**, served by the add-on. Everything you do there is saved to one YAML file, `streamdeck.yaml`, in HA's config folder, so you can also edit that file by hand (File Editor or any text editor) and HA's backups include it. The renderer reloads the file the moment it changes.

## The setup page

- **Panels list** (left). A panel that isn't in the config yet appears as "New panel found" with the ID it shows on its keys. You give it a name and size, and it's added.
- **The panel itself** (middle), drawn as it looks right now, with a tab per page. "+ Add page" adds one, × on a tab deletes it (after a confirm). Drag one key onto another to swap them. "Try it" mode lets you tap keys as if it were the real panel.
- **Settings** (right), in three tabs:
  - **Panel**: name, size (Mini, Module 6, 15 or 32), start page, brightness, remove.
  - **Page**: name, start page, a picture or clock across the keys, what tapping an empty key does, the page's own brightness, delete.
  - **Key**: what the button does, its label, and its look for each state.
- **Show the YAML** at the bottom shows exactly what Save writes.

## Finding and naming panels

A new ESP32 joins Wi-Fi and announces itself (mDNS) with a short ID from its hardware address, for example `sd-3c71bf`, plus the deck model. Until a panel is in the config, its keys show that ID. Once named, everything refers to the name, so replacing a dead ESP32 means changing one `id:` line. The deck reports its size, which pre-fills the size setting.

## The file

A full, checked example is in [`examples/streamdeck.yaml`](../examples/streamdeck.yaml). The shape:

```yaml
panels:
  hallway:
    id: sd-3c71bf
    size: mini              # mini, module6, module15, module32, plus (Stream Deck +)
    brightness: 70          # or a schedule, see below
    start_page: home        # defaults to "home"
    pages:
      home:
        buttons:
          - key: 1          # keys count from 1, left to right, top to bottom
            entity: light.hallway
            text: Hall
            when:
              "on": { icon: mdi:lightbulb, background: amber, icon_color: dark }
              "off": { icon: mdi:lightbulb-outline, background: black, icon_color: grey }
styles:                     # optional looks to reuse with style: name
  light: { icon: mdi:lightbulb }
```

Each panel has its own pages; page names only need to be unique within a panel.

### Button types

| In the setup page | In the file |
|---|---|
| Control a device | `entity: light.x`; `tap:` is `toggle` (default), `turn_on`, `turn_off` or `none` ("Do nothing", for a key that only works when held or double pressed) |
| Control a room or floor | `lights: { area: kitchen }` or `lights: { floor: ground_floor }`, with `tap:` as above. Toggle means: if any light is on, turn them all off, otherwise turn them all on. Rooms and floors are HA's Areas and Floors. |
| Run a script or scene | `entity: script.x` with `tap: { action: script.turn_on }` (or `scene.turn_on`) |
| Go to another page | `tap: { go_to: page_name }` (same panel) |
| Go back | `tap: back` |
| Run a timer | `entity: timer.x` (an HA Timer helper). While it runs, the key shows the time left in place of its icon, counting down every second, with a bar along the top for how much is left. Tap starts it, pauses it while it runs, and resumes it when paused; `turn_off` (e.g. `hold: turn_off`) cancels it. `when:` looks use the states `active`, `paused` and `idle`. `countdown: false` keeps the icon instead of the time left. In the setup page this is "Run a timer". |
| Show a sensor | `entity: sensor.x`, `tap: none`, and the value in `text:` |
| Any HA action | `tap: { action: domain.action, target: {...}, data: {...} }`, written as in an HA automation |

Any button can also have:

- `hold:`, a long press: what holding the key for half a second does. Same choices as `tap:`.
  For a light (`entity: light.x`) or `lights:`, `hold:` can also be `dim`, `brighten` or `dim_brighten`: the brightness changes 10% every 0.35 s for as long as the key is held. `dim_brighten` goes the other way from the last hold (the first hold dims if the lights are over half bright). Dimming stops at 1% instead of turning lights off. For a room or floor, only the lights that are on change, unless they're all off, when brightening brings them all up from 0.
- `double:`, a double press: what two quick presses do (the second within 0.3 s of letting go). Same choices as `tap:`. Only keys with `double:` wait those 0.3 s before running their tap; every other key runs at once.
- `asleep: true`: runs on the first press even while the panel sleeps on another page (see Sleep Mode below).
- `confirm: true` (press twice to run). It applies to each kind of press: a long press needs a second long press, and so on.

Keys with a long press show a small dash in their top right corner, and keys with a double press show two dots (bottom right when the label is on top).

In the setup page these are the "Long press" and "Double press" boxes under each key; their options show once the box is ticked. To run a script or scene on a long or double press, give it as `{ action: script.turn_on, target: { entity_id: script.x } }`.

### Labels and looks

- `text:` is the label. It can use HA templates, e.g. `"{{ states('sensor.outside_temperature') }}°"`; the renderer asks HA to evaluate them and redraws when the value changes. `text_position:` is `bottom` (default), `top` or `none`.
- A look is `icon` (`mdi:` name or a .png/.svg file), `background` and `icon_color` (colour names `black, grey, dark, white, amber, blue, green, red, purple`, or `#rrggbb`).
- `when:` gives a look per state of the entity (`"on"`/`"off"`, `open`/`closed`, `locked`/`unlocked`, ...). For room and floor buttons the states are `any_on` and `all_off`. Without `when:`, the look goes on the button itself.

### Pictures and the clock

A page can have `image:` across all its keys; buttons on the page are drawn on top.

- `{ type: clock, style: digit_per_key, font: montserrat, hours: 24 }`: one digit per key, so the gaps between keys never cut through a number. Digits keep their natural shapes at one size, as large as the key allows; the colon is two small dots. Fonts: `montserrat` (default), `archivo`, `roboto`, `oswald`, `bebas`. `style: across` draws one big clock over the whole panel instead (harder to read, since the gaps cut the digits). `hours: 12` shows 7:41 with AM/PM instead of 19:41.
- `{ type: picture, file: /config/streamdeck/images/x.png }`, cropped to fill the panel.
- `{ type: camera, entity: camera.x, refresh: 10 }`, slow on the Mini.

### Dials (Stream Deck +)

A `size: plus` panel has 8 keys and 4 dials, with a touch strip above the dials. Each page can
give each dial an entity to turn; the strip above it shows its name, value and a level bar.

```yaml
        dials:
          - dial: 1                 # dials count from 1, left to right
            entity: light.desk      # turning sets the brightness
          - dial: 2
            entity: media_player.office
            text: Music             # the name on the strip (default: the entity's name)
            step: 5                 # one click of the dial: 5% here (default 2% for volume)
          - dial: 3
            entity: climate.hallway
            press: { go_to: heating }   # pushing the dial, or tapping the strip above it
```

| Entity | Turning sets | One click by default |
|---|---|---|
| `light.` | brightness (down to 0 turns it off) | 5% |
| `media_player.` | volume | 2% |
| `cover.` | position | 5% |
| `climate.` | target temperature | its own step, usually 0.5° |
| `fan.` | speed | its own step, usually 10% |
| `input_number.`, `number.` | the value | the helper's step |
| `timer.` | idle: how long the next start runs; running: the time left (restarts it with the new time, never under 1 minute) | 1 minute |

A timer's strip counts down every second while it runs. The length a dial picks for an idle
timer stays until it's turned again (or the add-on restarts, which goes back to the helper's own
duration), and a timer key for the same timer starts it with that length too.

Anything else shows its state and turning does nothing. `press:` takes the same choices as a
button's `tap:`; by default it toggles the entity (play/pause for a media player, nothing for
a number or sensor). Tapping the strip above a dial does the same as pushing it, and swipes do
nothing. `text:` can use templates, like a button's label. `icon:` and `color:` (the bar's
colour) default to suit the entity. A page without dials leaves the strip dark.

### Brightness and sleep

Brightness is one backlight for the whole panel, so it can't differ per key. It can change at any time:

```yaml
    brightness:
      default: 70
      schedule:                       # each step lasts until the next one starts
        - { at: "07:00", level: 70 }
        - { at: sunset, level: 40 }
        - { at: "22:30", level: 15 }
    # or: brightness: { follow: input_number.hallway_panel }  (an HA slider your automations set)
    sleep:                            # Sleep Mode: changes page and brightness when inactive
      after_minutes: 10               # sleep after 10 minutes with no presses
      between: ["22:00", "07:00"]     # and/or sleep during these hours
      awake_seconds: 60               # woken during those hours, sleep again after 60 s with no presses (default 60)
      page: clock                     # page to show while asleep (leave out to stay on the current page)
      brightness: 5                   # how dim
```

Sleep needs at least one of the two triggers: `after_minutes:`, `between:`, or both (either one puts it to sleep). A page can also set its own `brightness:`, which applies while it's showing. Any press (or turning a dial, or touching the strip) wakes a sleeping panel at its normal brightness. If the panel still shows its own buttons while asleep (no sleep `page:`), the key pressed also runs, so a dimmed button works as it looks. If it shows another page, such as a clock, the first press only wakes it, except on keys marked `asleep: true`, which run straight away (for example the bedside lamp key). Inside the `between` hours it falls asleep again after `awake_seconds` with no presses (60 by default, 5 to 3600); `after_minutes` applies outside those hours.

## Catching mistakes

`python -m renderer check streamdeck.yaml` (and the setup page, on every save) reports each problem with its line number, for example `line 12: 'entitty' isn't a setting here. Check the spelling.` It catches unknown settings, keys and dials beyond the panel's size, keys and dials used twice, pages and styles that don't exist, duplicate panel IDs, buttons that toggle without an entity or room, and bad colours, fonts, times and icons. When a saved file has a mistake, the panels keep the last good config.

