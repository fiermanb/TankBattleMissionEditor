# Tank Battle Classic Mission Editor

Mission and map editor for the Windows game *Tank Battle Classic* (Steam). Edits your
own installed copy. This unofficial application does not contain any game content and
is not affiliated with the game's developer or publisher. Buy the game from
https://store.steampowered.com/app/5086560/Tank_Battle_Classic/

![Main window: mission map with a selected tank and its route, layers, spawn table and properties](docs/screenshot-main.png)

![Events and trigger areas: the Objects pane, an event with its trigger area, and the events table](docs/screenshot-events.png)

![Terrain features: trenches, tank pits, craters, a road and an embankment drawn with brushes; a selected trench, its properties and the features table](docs/screenshot-terrain.png)

## Start

Unzip the release into a folder of your own and run `TankBattleMissionEditor.exe`
(or `python editor.py` from source, see Build). The editor finds the game in your
Steam libraries or asks for its folder (File > Select game folder). Double-click
a mission in the Catalog. **Close the game before saving**; restart it to see
changes.

## What it does

- Edit tanks, waypoints, routes and scenery of any mission.
- Tank and mission settings: type, side, AI behaviour, respawns, fog, and
  day, dusk or night lighting for custom scenarios.
- Events with timers, destroyed tanks or trigger areas, for example waves.
- New scenarios from a mission or on an empty terrain, with a choice of player tanks.
- Terrain features for custom scenarios: trenches, tank pits, roads and more,
  drawn as lines or points with ready-made or own brushes, editable on the map
  and in their table.
- AI tanks of custom scenarios drive around changed terrain and objects.
- Back up, export and import scenarios.

## Tools and controls

One map tool is active at a time. It stays active until you choose another
tool. Its toolbar button stays pressed, and the status bar shows
its instructions. Esc, right-click > Stop, or a second click on the pressed
button returns to Select / move.

| Input | Action |
| --- | --- |
| Click / Shift+click / Ctrl+click | select / add / toggle |
| Drag on empty map / drag selection (Select / move) | selection box / move |
| Right-click | context menu; finishes a drawing |
| Wheel / middle or right drag, or Space held | zoom / pan (in any tool) |
| Drag (Draw terrain) | draw freehand |
| Double-click or Enter / Backspace / Esc while drawing | finish / remove the last point / cancel |
| Double-click a feature's line (Edit terrain features) | add a point |
| Ctrl+X / C / V | cut / copy / paste (at the pointer) |
| Del, Ctrl+D, Q / E | delete, duplicate, rotate |
| Ctrl+Z, Ctrl+S, Ctrl+A | undo, save, select all |
| Esc | cancel; else deselect; else back to Select / move |

Help > Controls lists all shortcuts.

## Backups and removal

The first save of a level keeps the original (File > Restore original).

Scenario > Remove scenario removes one scenario (also: right-click it in the
Catalog). Scenario > Remove all custom scenarios removes all of them and undoes
text changes to the original missions. Steam's "Verify integrity of game
files" restores the game.

Backups and settings are kept next to the program, in `backups\` and
`settings.json` (Help > Open backup folder). To update, unzip the new version
over the old `TankBattleMissionEditor` folder; the backups stay. If the program
folder cannot be written (for example under Program Files), the editor uses
`%LOCALAPPDATA%\TankBattleMissionEditor` instead.

Custom scenarios are backed up on every save. A game update removes them from
the game; the editor then offers to restore them, converted to the new game
version (also: Scenario > Restore scenarios from backup).

Scenario > Export scenario saves a scenario as one `.tbscenario` file to share;
Scenario > Import scenario installs one. The file holds no game files, only the
map and the names of the game assets it uses: the recipient needs the game and
this editor, also after a game update. Deleted objects, including the
switched-off objects of the mission a scenario was made from, are left out.

## Included scenarios

Two scenarios made with the editor come with it (`Scenarios` folder of the
release, `scenarios/` in the source). Install them with Scenario > Import
scenario; they appear on the Custom Missions page.

- **Kursk - Deep Defense**: July 1943. Hold three trench lines with a T-34
  company against 125 German tanks in three waves. Defenders fall back when a
  line is breached.
- **Kursk - Breakthrough**: the same battle from the German side. Attack in a
  Panzer IV and break through all three Soviet lines.

Both offer every tank of the game as the player's tank.

## Limitations

- Only the game's existing objects and tank types.
- Every map uses one of the game's terrains. A custom scenario can repaint and
  reshape it, but cannot change its size.
- Terrain editing and the AI route map rebuild work on custom scenarios only.
- Saving a custom scenario rebuilds its AI route map when terrain or objects
  changed. The rebuilt map is close to the game's own; in dense city and
  mountain areas AI routes can differ.
- Event messages longer than 45 characters can be cut off in the game.
- The briefing screen lists at most 9 unit groups.
- After a game update, objects whose scripts changed cannot be edited until a
  new version of the editor is released.

## Build

Requires Python 3.11+ on Windows.

    pip install -r requirements.txt
    python editor.py                      # run from source
    python -m unittest discover tests     # tests (need the installed game)
    python packaging/build_exe.py         # standalone executable + release zip

## Licence

Copyright (C) 2026 fierman. GNU General Public License version 3, see
`LICENSE`. Bundled third-party components: see the release's
`THIRD-PARTY-NOTICES.txt`.
