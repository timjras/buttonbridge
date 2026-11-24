# Changelog

## 0.6.2

- Setup page: "Run a timer" is its own kind of key, with a "Show the time left" option
  (`countdown: false` turns it off), a look for when it's paused, and a preview of the countdown.
  Long press can be set to "Cancel the timer". Timers can be picked for dials too.

## 0.6.1

- Timer keys: a key for an HA timer (`entity: timer.x`) shows the time left, counting down
  every second, with a bar for how much is left. Tapping starts, pauses and resumes it; `turn_off` cancels it.
- Timer dials (Stream Deck +): turning sets how long the timer runs next, or adds and takes away
  minutes while it runs. The strip counts down too.

## 0.6.0

- Renamed to ButtonBridge. Existing installs keep their settings and `streamdeck.yaml`.
- The app can be installed straight from the GitHub repository.

## 0.5.2

- Setup page: drag a Stream Deck + dial onto another to swap them.

## 0.5.1

- A sleeping panel no longer swallows the first key press on keys that run while asleep.
- Bridges reconnect faster after a dropped connection.

## 0.5.0

- Stream Deck + support: dials and the touch strip.
