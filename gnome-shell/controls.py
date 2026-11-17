"""Shared desktop dimensions and input contracts."""

import re

WIDTH, HEIGHT = 1024, 640
MAX_EVENTS = 48
BUTTON_CODES = (272, 274, 273)  # Linux BTN_LEFT, BTN_MIDDLE, BTN_RIGHT
COORDS = {
    "x": {"type": "number", "minimum": 0, "exclusiveMaximum": WIDTH},
    "y": {"type": "number", "minimum": 0, "exclusiveMaximum": HEIGHT},
}
KEY = {"type": "string", "minLength": 1, "maxLength": 32}
BUTTON = {"type": "integer", "minimum": 0, "maximum": 2}
PRESSED = {"type": "boolean"}
SCROLL = {
    "axis": {"type": "integer", "enum": [0, 1]},
    "steps": {"type": "integer", "minimum": -8, "maximum": 8},
}
EVENTS = {
    "move": COORDS,
    "button": {"button": BUTTON, "pressed": PRESSED},
    "key": {"key": KEY, "pressed": PRESSED},
    "scroll": SCROLL,
    "release": {},
}
ACTIONS = {
    "move": COORDS,
    "click": {**COORDS, "button": BUTTON},
    "drag": {**COORDS, "to_x": COORDS["x"], "to_y": COORDS["y"]},
    "scroll": {**COORDS, **SCROLL},
    "press_keys": {"keys": {"type": "array", "items": KEY, "minItems": 1, "maxItems": 4}},
    "type_text": {"text": {"type": "string", "minLength": 1, "maxLength": 96}},
}


def obj(properties, *, optional=()):
    return {"type": "object", "properties": properties,
            "required": [key for key in properties if key not in optional], "additionalProperties": False}


def variant(shapes):
    return {"anyOf": [obj({"type": {"type": "string", "const": name}, **fields})
                      for name, fields in shapes.items()]}


def key_names(names):
    for name in names:
        if not isinstance(name, str) or not name:
            raise ValueError("Keys must be characters or XKB keysym names.")
        if len(name) == 1:
            if name.isprintable():
                continue
        elif re.fullmatch(r"[A-Za-z0-9_]{2,32}", name, re.ASCII):
            continue
        raise ValueError("Keys must be printable characters or XKB keysym names.")


def validate_shaped(value, shapes):
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise ValueError("An input operation needs a type.")
    kind = value["type"]
    fields = shapes.get(kind)
    if fields is None or set(value) != {"type", *fields}:
        raise ValueError("Input fields do not match the operation.")
    for name, rules in fields.items():
        item = value[name]
        match rules["type"]:
            case "number":
                valid = type(item) in (float, int) and 0 <= item < rules["exclusiveMaximum"]
            case "integer":
                valid = type(item) is int and (item in rules["enum"] if "enum" in rules
                                              else rules["minimum"] <= item <= rules["maximum"])
            case "string":
                valid = isinstance(item, str) and rules["minLength"] <= len(item) <= rules["maxLength"]
            case "boolean":
                valid = type(item) is bool
            case "array":
                valid = isinstance(item, list) and rules["minItems"] <= len(item) <= rules["maxItems"]
        if not valid:
            raise ValueError(f"Invalid {name} for {kind}.")


def validate_events(events):
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError(f"Supply 1–{MAX_EVENTS} browser input events.")
    for event in events:
        validate_shaped(event, EVENTS)
    key_names(e["key"] for e in events if e["type"] == "key")
    return events


def action_events(action):
    validate_shaped(action, ACTIONS)
    kind = action["type"]
    if kind == "press_keys":
        keys = action["keys"]
        key_names(keys)
        if len(set(keys)) != len(keys):
            raise ValueError("A key chord must contain distinct keys.")
        return [key_event(key, True) for key in keys] + [key_event(key, False) for key in reversed(keys)]
    if kind == "type_text":
        text = action["text"]
        if not text.isprintable():
            raise ValueError("Use printable characters. Send Return or Tab as a separate key action.")
        return [key_event(char, pressed) for char in text for pressed in (True, False)]
    x, y = action["x"], action["y"]
    events = [move(x, y)]
    if kind == "click":
        button = action["button"]
        events += [button_event(button, True), button_event(button, False)]
    elif kind == "drag":
        events.append(button_event(0, True))
        for step in range(1, 9):
            events.append(move(x + (action["to_x"] - x) * step / 8,
                               y + (action["to_y"] - y) * step / 8))
        events.append(button_event(0, False))
    elif kind == "scroll" and action["steps"]:
        events.append({"type": "scroll", "axis": action["axis"], "steps": action["steps"]})
    return events


def key_event(key, pressed):
    return {"type": "key", "key": key, "pressed": pressed}


def button_event(button, pressed):
    return {"type": "button", "button": button, "pressed": pressed}


def move(x, y):
    return {"type": "move", "x": x, "y": y}
