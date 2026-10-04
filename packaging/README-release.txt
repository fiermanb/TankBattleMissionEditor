TANK BATTLE CLASSIC MISSION EDITOR
==================================

Mission editor for the Windows game Tank Battle Classic (Steam). Edits your own
installed copy; contains no game content. Unofficial, not affiliated with the
game's developer or publisher.

START
-----
Unzip and run TankBattleMissionEditor.exe (no installation needed). The editor
finds the game in your Steam libraries or asks for its folder (File > Select
game folder). Double-click a mission in the Catalog.
Close the game before saving; restart it to see changes.

WHAT IT DOES
------------
- Move, rotate, add, copy and delete tanks, waypoints and scenery.
- Tank settings: type, side, AI behaviour, respawns, unit group, follow target.
- Mission settings (nothing selected): fog, visibility, recon plane.
- Objects pane: new tanks, routes, waypoints and events, and objects from
  any mission. Double-click places at the map centre, or drag onto the map.
  A new route takes its waypoints click by click; Enter, Esc or right-click
  finishes. Right-click > Add here places at the pointer.
- Table Of Contents: double-click opens a layer's table; right-click
  selects, zooms or shows only that layer.
- Tables of spawns, events, waypoints and scenery: double-click a cell to
  edit it.
- New scenarios from a mission or on an empty terrain (Scenario menu); they
  appear on a "Custom Missions" page.

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
are in %LOCALAPPDATA%\TankBattleMissionEditor. Steam's "Verify integrity of game
files" also restores everything. After a game update the editor sets the old
backups aside and lists custom scenarios that the update removed.

LIMITATIONS
-----------
AI tanks do not route around added or moved buildings. Only existing objects
and tank types; terrain cannot be edited. If a virus scanner blocks the
program, check it with your scanner and allow it.

LICENCE
-------
Copyright (C) 2026 fierman. Free software under the GNU General Public License
version 3 (LICENSE.txt); it comes with ABSOLUTELY NO WARRANTY. Licences of the
bundled components: THIRD-PARTY-NOTICES.txt.
