# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""What each event type and trigger type of Event_Controller_CS uses.

One description per event type and per trigger type: which fields apply, with
label, input kind, unit and help text. The Properties panel and the events
table are built from it, so they show only fields that do something in the
game. Confirmed in the game (Trigger Test, rounds 1-10):

  - only message events (show message, mission complete, mission failed)
    show their message; text on other types is ignored,
  - a 'Change AI settings' event applies all its New_ values to the affected
    tanks, not only changed ones,
  - the trigger time works for timers only: with 'tanks destroyed' and area
    triggers the event fires when the condition is met (a delay needs an
    'Enable events' event that starts timed events),
  - tanks needed: All_Trigger_Flag on = all listed tanks, off = the number in
    Necessary_Num; Operator_Type 'any listed tank' overrides both,
  - message colour and sound work; 'Activate object' switches scene objects
    on and off.

Field descriptions are tuples (key, label, kind, unit, help); kind is one of
'bool', 'int', 'float', 'text'.
"""

MESSAGE_TYPES = (1, 10, 11)
MESSAGE_LIMIT = 45
TARGET_TYPES = (2, 3, 6)
EVENT_NAMES = {1: "Show message", 2: "Change AI settings", 3: "Remove tanks", 4: "Artillery fire",
               6: "Destroy target", 7: "Change lighting", 10: "Mission complete", 11: "Mission failed",
               12: "Activate object", 13: "Enable events"}
EVENT_HELP = {
    1: "Shows a message.",
    2: "Gives the affected tanks new AI settings. All values below are applied, not only changed ones.",
    3: "Removes the affected tanks from the battle.",
    4: "Fires an artillery barrage.",
    6: "Destroys the affected tanks.",
    7: "Changes fog density and visibility over a set time.",
    10: "Ends the mission as won and shows the message.",
    11: "Ends the mission as lost and shows the message.",
    12: "Switches an object in the scene on or off.",
    13: "Starts events that are not enabled at start; their timers begin now.",
}
TRIGGER_NAMES = {0: "Timer", 1: "Tanks destroyed", 2: "Tank in trigger area"}
TRIGGER_HELP = {
    0: "Fires the given number of seconds after the event is enabled: at the start of the mission, or "
       "when an 'Enable events' event enables it.",
    1: "Fires when the listed tanks are destroyed (all, any, or a number of them).",
    2: "Fires when a tank enters one of the trigger areas.",
}
AREA_TANKS = {0: "Listed tanks", 1: "Any hostile tank", 2: "Any friendly tank"}
NEEDED = {"all": "All listed tanks", "any": "Any listed tank", "count": "A number of them"}

TYPE_FIELDS = {
    4: [("Artillery_Num", "Shells", "int", "", "Number of shells in the barrage")],
    7: [("Visibility", "New visibility", "float", "m", "How far tanks can see each other afterwards"),
        ("Fog_Density", "New fog density", "float", "", "0.0005 light haze, 0.01-0.025 dense fog"),
        ("Change_Lighting_Duration", "Change over", "float", "s", "Seconds for the change")],
    12: [("Activate_Flag", "Switch on (off: switch off)", "bool", "", "On shows the object, off hides it")],
}
AI_FIELDS = [
    ("New_Breakthrough", "Breakthrough", "bool", "", "Drive the route without stopping to fight"),
    ("New_No_Attack", "No attack", "bool", "", "Do not attack (the tank may still return fire)"),
    ("New_Patrol_Type", "Patrol type", "int", "", "Route behaviour (0 or 1)"),
    ("New_Patrol_Speed_Rate", "Patrol speed rate", "float", "", "Speed on the route; 1 = normal"),
    ("New_Combat_Speed_Rate", "Combat speed rate", "float", "", "Speed in combat; 1 = normal"),
    ("New_Follow_Distance", "Follow distance", "float", "m", "Distance kept to the follow target"),
    ("New_Follow_Closest_Enemy", "Follow closest enemy", "bool", "", ""),
    ("New_Follow_Player_Closest_Enemy", "Follow player's enemy", "bool", "", ""),
    ("New_Through_Hedges", "Through hedges", "bool", "", "Drive through hedges"),
    ("New_Approach_Distance", "Approach distance", "float", "m",
     "Distance to which the tank closes in on an enemy; inf = holds its position"),
    ("New_Attack_Distance", "Attack distance", "float", "m", ""),
    ("New_OpenFire_Distance", "Open fire distance", "float", "m", "Distance from which the tank fires"),
    ("New_Lost_Count", "Lost count", "float", "s", "Time before a lost enemy is given up"),
    ("New_Dead_Angle", "Dead angle", "float", "deg", ""),
    ("New_Extended_Visibility", "Extra visibility", "float", "m", ""),
    ("New_Max_Lead_Multiplier", "Max lead multiplier", "float", "", "Aiming ahead of moving targets"),
    ("New_Is_Mission_Target", "Mission target", "bool", "", "Counts as a mission target"),
    ("New_Respawn_Times", "Respawn times", "int", "", ""),
    ("New_Auto_Respawn_Interval", "Respawn interval", "float", "s", ""),
    ("New_Respawn_Min_Dist", "Respawn min. distance", "float", "m", ""),
    ("New_Respawn_Max_Dist", "Respawn max. distance", "float", "m", ""),
]
MESSAGE_FIELDS = [
    ("Event_Message_Time", "Shown for", "float", "s", "Seconds the message stays on screen"),
]


def label(field):
    """'Shown for (s)': the label with its unit."""
    _, text, _, unit, _ = field
    return f"{text} ({unit})" if unit else text


def shows_message(event_type):
    return event_type in MESSAGE_TYPES


def ai_source_key(new_key):
    """The spawn field a New_ field of a Change AI event replaces."""
    return new_key[4:] if new_key.startswith("New_") else new_key


def tanks_needed(d):
    """('all' | 'any' | 'count', number) of an event's trigger tank rule."""
    if d.get("Operator_Type") == 1:
        return "any", 1
    if d.get("All_Trigger_Flag", True):
        return "all", 0
    return "count", int(d.get("Necessary_Num", 1))


def needed_fields(mode, number=1):
    """Field values for a trigger tank rule (see tanks_needed())."""
    if mode == "any":
        return {"Operator_Type": 1, "All_Trigger_Flag": True}
    if mode == "count":
        return {"Operator_Type": 0, "All_Trigger_Flag": False, "Necessary_Num": max(1, int(number))}
    return {"Operator_Type": 0, "All_Trigger_Flag": True}


def uses_tank_list(d):
    """True when the event's trigger reads its tank list."""
    t = d.get("Trigger_Type")
    return t == 1 or (t == 2 and d.get("Trigger_Setting_Type", 0) == 0)


__all__ = ["MESSAGE_TYPES", "MESSAGE_LIMIT", "TARGET_TYPES", "EVENT_NAMES", "EVENT_HELP", "TRIGGER_NAMES",
           "TRIGGER_HELP", "AREA_TANKS", "NEEDED", "TYPE_FIELDS", "AI_FIELDS", "MESSAGE_FIELDS", "label",
           "shows_message", "ai_source_key", "tanks_needed", "needed_fields", "uses_tank_list"]
