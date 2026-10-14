TANK BATTLE CLASSIC MISSION EDITOR
==================================

Mission editor for the Windows game Tank Battle Classic (Steam). Edits your own
installed copy; contains no game content. Unofficial, not affiliated with the
game's developer or publisher.

START
-----
Unzip into a folder of your own (for example Documents or a games folder, not
Program Files) and run TankBattleMissionEditor.exe (no installation needed).
The editor finds the game in your Steam libraries or asks for its folder
(File > Select game folder). Double-click a mission in the Catalog.
Close the game before saving; restart it to see changes.

UPDATING: unzip the new version over the old TankBattleMissionEditor folder.
Your backups and settings in that folder stay.

WHAT IT DOES
------------
- Move, rotate, add, copy and delete tanks, waypoints and scenery.
- Tank settings: type, side, AI behaviour, respawns, unit group, follow target.
- Mission settings (nothing selected): fog, visibility, recon plane.
- Objects pane: new tanks, routes, waypoints, events, trigger areas and
  objects from any mission. Click an item to choose the Add tool for it, then
  click on the map to place one with each click (or drag an item onto the
  map). A new route takes its waypoints click by click; right-click,
  double-click or Enter finishes. Esc, right-click > Stop or the Select tool
  ends a tool. Right-click > Add here places at the pointer.
- Table Of Contents: double-click opens a layer's table; right-click
  selects, zooms or shows only that layer.
- Terrain tools (toolbar) for custom scenarios (a scenario gets its own copy
  of the terrain; the original missions keep theirs): Draw terrain draws with
  the brush chosen on the Terrain tab (trench, tank pit, crater, road and
  others, or brushes of your own) along clicked or dragged points; Edit
  terrain features moves, reshapes or deletes a drawn feature. Elevation
  tint and contour lines are in the Table Of Contents.
  Saving rebuilds the AI route map when terrain or objects changed, so AI
  tanks drive around them.
- F5 starts the game.
- Tables of spawns, events, waypoints and scenery: double-click a cell to
  edit it.
- New scenarios from a mission or on an empty terrain (Scenario menu); they
  appear on a "Custom Missions" page.

INCLUDED SCENARIOS
------------------
The Scenarios folder holds two scenarios made with the editor: Kursk - Deep
Defense (hold three trench lines against three German waves) and Kursk -
Breakthrough (attack the same lines with the first German wave). Install them
with Scenario > Import scenario.

CONTROLS
--------
Click / Shift+click / Ctrl+click: select / add / toggle.
Drag on empty map: selection box. Drag a selection: move.
Right-click: menu. Middle or right drag: pan. Wheel: zoom.
Ctrl+X/C/V: cut/copy/paste. Del: delete. Ctrl+Z: undo. Ctrl+S: save.
Help > Controls lists all shortcuts.

BACKUPS
-------
The first save of a level keeps the original (File > Restore original).
Scenario > Remove custom scenarios undoes all scenarios. Backups and settings
are kept next to the program, in the backups folder and settings.json
(Help > Open backup folder). If the program's folder cannot be written, they
are kept in %LOCALAPPDATA%\TankBattleMissionEditor. Steam's "Verify integrity
of game files" also restores everything.

Custom scenarios are backed up on every save. A game update removes them from
the game; the editor then offers to restore them, converted to the new game
version (also: Scenario > Restore scenarios from backup).

Scenario > Export scenario saves a scenario as one .tbscenario file to share;
Scenario > Import scenario installs one. The file holds no game files, only
the map and the names of the game assets it uses: the recipient needs the
game and this editor, in any game version.

LIMITATIONS
-----------
Only existing objects and tank types. Terrain editing and the AI route map
rebuild work on custom scenarios only; the rebuilt map is close to the game's
own. If a virus scanner blocks the program, check it with your scanner and
allow it.

LICENCE
-------
Copyright (C) 2026 fierman. Free software under the GNU General Public License
version 3 (LICENSE.txt); it comes with ABSOLUTELY NO WARRANTY. Licences of the
bundled components: THIRD-PARTY-NOTICES.txt.
