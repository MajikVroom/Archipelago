from __future__ import annotations

import typing
import enum
import warnings
from json import JSONEncoder, JSONDecoder

import websockets

from Utils import ByValue, Version
from dataclasses import dataclass, is_dataclass, asdict


class JSONMessagePart(typing.TypedDict, total=False):
    text: str
    # optional
    type: str
    color: str
    # owning player for location/item
    player: int
    # if type == item indicates item flags
    flags: int


class ClientStatus(ByValue, enum.IntEnum):
    CLIENT_UNKNOWN = 0
    CLIENT_CONNECTED = 5
    CLIENT_READY = 10
    CLIENT_PLAYING = 20
    CLIENT_GOAL = 30


class SlotType(ByValue, enum.IntFlag):
    spectator = 0b00
    player = 0b01
    group = 0b10

    @property
    def always_goal(self) -> bool:
        """Mark this slot as having reached its goal instantly."""
        return self.value != 0b01


class Permission(ByValue, enum.IntFlag):
    disabled = 0b000  # 0, completely disables access
    enabled = 0b001  # 1, allows manual use
    goal = 0b010  # 2, allows manual use after goal completion
    auto = 0b110  # 6, forces use after goal completion, only works for release
    auto_enabled = 0b111  # 7, forces use after goal completion, allows manual use any time

    @staticmethod
    def from_text(text: str):
        data = 0
        if "auto" in text:
            data |= 0b110
        elif "goal" in text:
            data |= 0b010
        if "enabled" in text:
            data |= 0b001
        return Permission(data)


class NetworkPlayer(typing.NamedTuple):
    """Represents a particular player on a particular team."""
    team: int
    slot: int
    alias: str
    name: str


class NetworkSlot(typing.NamedTuple):
    """Represents a particular slot across teams."""
    name: str
    game: str
    type: SlotType
    group_members: typing.Union[typing.List[int], typing.Tuple] = ()  # only populated if type == group


class NetworkItem(typing.NamedTuple):
    item: int
    location: int
    player: int
    flags: int = 0


def _scan_for_TypedTuples(obj: typing.Any) -> typing.Any:
    if isinstance(obj, tuple) and hasattr(obj, "_fields"):  # NamedTuple is not actually a parent class
        data = obj._asdict()
        data["class"] = obj.__class__.__name__
        return data
    if is_dataclass(obj):
        data = asdict(obj)
        data["class"] = obj.__class__.__name__
        return data
    if isinstance(obj, (tuple, list, set, frozenset)):
        return tuple(_scan_for_TypedTuples(o) for o in obj)
    if isinstance(obj, dict):
        return {key: _scan_for_TypedTuples(value) for key, value in obj.items()}
    return obj


_encode = JSONEncoder(
    ensure_ascii=False,
    check_circular=False,
    separators=(',', ':'),
).encode


def encode(obj: typing.Any) -> str:
    return _encode(_scan_for_TypedTuples(obj))


def get_any_version(data: dict) -> Version:
    data = {key.lower(): value for key, value in data.items()}  # .NET version classes have capitalized keys
    return Version(int(data["major"]), int(data["minor"]), int(data["build"]))


allowlist = {
    "NetworkPlayer": NetworkPlayer,
    "NetworkItem": NetworkItem,
    "NetworkSlot": NetworkSlot
}

custom_hooks = {
    "Version": get_any_version
}


def _object_hook(o: typing.Any) -> typing.Any:
    if isinstance(o, dict):
        hook = custom_hooks.get(o.get("class", None), None)
        if hook:
            return hook(o)
        cls = allowlist.get(o.get("class", None), None)
        if cls:
            for key in tuple(o):
                if key not in cls._fields:
                    del (o[key])
            return cls(**o)

    return o


decode = JSONDecoder(object_hook=_object_hook).decode


class Endpoint:
    socket: websockets.WebSocketServerProtocol

    def __init__(self, socket):
        self.socket = socket


class HandlerMeta(type):
    def __new__(mcs, name, bases, attrs):
        handlers = attrs["handlers"] = {}
        trigger: str = "_handle_"
        for base in bases:
            handlers.update(base.handlers)
        handlers.update({handler_name[len(trigger):]: method for handler_name, method in attrs.items() if
                         handler_name.startswith(trigger)})

        orig_init = attrs.get('__init__', None)
        if not orig_init:
            for base in bases:
                orig_init = getattr(base, '__init__', None)
                if orig_init:
                    break

        def __init__(self, *args, **kwargs):
            if orig_init:
                orig_init(self, *args, **kwargs)
            # turn functions into bound methods
            self.handlers = {name: method.__get__(self, type(self)) for name, method in
                             handlers.items()}

        attrs['__init__'] = __init__
        return super(HandlerMeta, mcs).__new__(mcs, name, bases, attrs)


class JSONTypes(str, enum.Enum):
    color = "color"
    text = "text"
    player_id = "player_id"
    player_name = "player_name"
    item_name = "item_name"
    item_id = "item_id"
    location_name = "location_name"
    location_id = "location_id"
    entrance_name = "entrance_name"


class JSONtoTextParser(metaclass=HandlerMeta):
    color_codes = {
        # not exact color names, close enough but decent looking
        "black": "000000",
        "red": "EE0000",
        "green": "00FF7F",
        "yellow": "FAFAD2",
        "blue": "6495ED",
        "magenta": "EE00EE",
        "cyan": "00EEEE",
        "slateblue": "6D8BE8",
        "plum": "AF99EF",
        "salmon": "FA8072",
        "white": "FFFFFF",
        "orange": "FF7700",
    }

    def __init__(self, ctx):
        self.ctx = ctx

    def __call__(self, input_object: typing.List[JSONMessagePart]) -> str:
        return "".join(self.handle_node(section) for section in input_object)

    def handle_node(self, node: JSONMessagePart):
        node_type = node.get("type", None)
        handler = self.handlers.get(node_type, self.handlers["text"])
        return handler(node)

    def _handle_color(self, node: JSONMessagePart):
        codes = node["color"].split(";")
        buffer = "".join(color_code(code) for code in codes if code in color_codes)
        return buffer + self._handle_text(node) + color_code("reset")

    def _handle_text(self, node: JSONMessagePart):
        return node.get("text", "")

    def _handle_player_id(self, node: JSONMessagePart):
        player = int(node["text"])
        node["color"] = 'magenta' if player == self.ctx.slot else 'yellow'
        node["text"] = self.ctx.player_names[player]
        return self._handle_color(node)

    # for other teams, spectators etc.? Only useful if player isn't in the clientside mapping
    def _handle_player_name(self, node: JSONMessagePart):
        node["color"] = 'yellow'
        return self._handle_color(node)

    def _handle_item_name(self, node: JSONMessagePart):
        flags = node.get("flags", 0)
        if flags == 0:
            node["color"] = 'cyan'
        elif flags & 0b001:  # advancement
            node["color"] = 'plum'
        elif flags & 0b010:  # useful
            node["color"] = 'slateblue'
        elif flags & 0b100:  # trap
            node["color"] = 'salmon'
        else:
            node["color"] = 'cyan'
        return self._handle_color(node)

    def _handle_item_id(self, node: JSONMessagePart):
        item_id = int(node["text"])
        node["text"] = self.ctx.item_names.lookup_in_slot(item_id, node["player"])
        return self._handle_item_name(node)

    def _handle_location_name(self, node: JSONMessagePart):
        node["color"] = 'green'
        return self._handle_color(node)

    def _handle_location_id(self, node: JSONMessagePart):
        location_id = int(node["text"])
        node["text"] = self.ctx.location_names.lookup_in_slot(location_id, node["player"])
        return self._handle_location_name(node)

    def _handle_entrance_name(self, node: JSONMessagePart):
        node["color"] = 'blue'
        return self._handle_color(node)


class RawJSONtoTextParser(JSONtoTextParser):
    def _handle_color(self, node: JSONMessagePart):
        return self._handle_text(node)


color_codes = {'reset': 0, 'bold': 1, 'underline': 4, 'black': 30, 'red': 31, 'green': 32, 'yellow': 33, 'blue': 34,
               'magenta': 35, 'cyan': 36, 'white': 37, 'black_bg': 40, 'red_bg': 41, 'green_bg': 42, 'yellow_bg': 43,
               'blue_bg': 44, 'magenta_bg': 45, 'cyan_bg': 46, 'white_bg': 47}


def color_code(*args):
    return '\033[' + ';'.join([str(color_codes[arg]) for arg in args]) + 'm'


def color(text, *args):
    return color_code(*args) + text + color_code('reset')


def add_json_text(parts: list, text: typing.Any, **kwargs) -> None:
    parts.append({"text": str(text), **kwargs})


def add_json_item(parts: list, item_id: int, player: int = 0, item_flags: int = 0, **kwargs) -> None:
    parts.append({"text": str(item_id), "player": player, "flags": item_flags, "type": JSONTypes.item_id, **kwargs})


def add_json_location(parts: list, location_id: int, player: int = 0, **kwargs) -> None:
    parts.append({"text": str(location_id), "player": player, "type": JSONTypes.location_id, **kwargs})


class Hint(typing.NamedTuple):
    receiving_player: int
    finding_player: int
    location: int
    item: int
    found: bool
    entrance: str = ""
    item_flags: int = 0

    def re_check(self, ctx, team) -> Hint:
        if self.found:
            return self
        found = self.location in ctx.location_checks[team, self.finding_player]
        if found:
            return Hint(self.receiving_player, self.finding_player, self.location, self.item, found, self.entrance,
                        self.item_flags)
        return self

    def __hash__(self):
        return hash((self.receiving_player, self.finding_player, self.location, self.item, self.entrance))

    def as_network_message(self) -> dict:
        parts = []
        add_json_text(parts, "[Hint]: ")
        add_json_text(parts, self.receiving_player, type="player_id")
        add_json_text(parts, "'s ")
        add_json_item(parts, self.item, self.receiving_player, self.item_flags)
        add_json_text(parts, " is at ")
        add_json_location(parts, self.location, self.finding_player)
        add_json_text(parts, " in ")
        add_json_text(parts, self.finding_player, type="player_id")
        if self.entrance:
            add_json_text(parts, "'s World at ")
            add_json_text(parts, self.entrance, type="entrance_name")
        else:
            add_json_text(parts, "'s World")
        add_json_text(parts, ". ")
        if self.found:
            add_json_text(parts, "(found)", type="color", color="green")
        else:
            add_json_text(parts, "(not found)", type="color", color="red")

        return {"cmd": "PrintJSON", "data": parts, "type": "Hint",
                "receiving": self.receiving_player,
                "item": NetworkItem(self.item, self.location, self.finding_player, self.item_flags),
                "found": self.found}

    @property
    def local(self):
        return self.receiving_player == self.finding_player

# Unlock conditions for an inner hint
class TriggerableHint(typing.NamedTuple):
    hint: TextHint | LocationSetHint
    trigger: LocationTrigger | FreeTrigger
    
    @staticmethod
    def index_all(ctx):
        for triggerable_hint in ctx.triggerable_hints:
            triggerable_hint.hint.index(ctx, triggerable_hint)
            triggerable_hint.trigger.index(ctx, triggerable_hint)
    
    @staticmethod
    def ensure_team_init(ctx, team):
        if not ctx.triggerable_hints:
            return
        all_team_data = ctx.triggerable_hint_state.setdefault("team_data", {})
        if team in all_team_data:
            return

        # Init all per-team triggerable hint data
        ctx.triggerable_hint_state["team_data"][team] = {}
        for triggerable_hint in ctx.triggerable_hints:
            triggerable_hint.hint.init_team_data(ctx, team, triggerable_hint)
            # No triggers currently create per-team data.

        FreeTrigger.release_all(ctx, team)
        TriggerableHint.broadcast_updates(ctx, team)

    @staticmethod
    def get_released_hints_for_type(ctx, team, player, hint_type) -> typing.List[TriggeredHint]:
        TriggerableHint.ensure_team_init(ctx, team)
        released_hints = []
        for triggered_hint in TriggeredHint.get_team_data_for_type(ctx, team, hint_type).keys():
            if player in triggered_hint.get_recipients() and triggered_hint.is_released(ctx, team):
                released_hints.append(triggered_hint.re_check(ctx, team))
        return released_hints
    
    @staticmethod
    def broadcast_updates(ctx, team):
        TriggerableHint.ensure_team_init(ctx, team)
        needed_updates = set()
        for (hint_type, data_for_type) in ctx.triggerable_hint_state["team_data"][team].items():
            for triggered_hint in data_for_type.keys():
                if triggered_hint.check_and_set_broadcasted(ctx, team):
                    for player in triggered_hint.get_recipients():
                        needed_updates.add((player, hint_type))
        
        for (player, hint_type) in needed_updates:
            ctx.on_changed_triggerable_hints(team, player, hint_type)

    def release(self, ctx, team):
        if self.hint.release(ctx, team):
            # Broadcast the hint to interested parties
            # Currently hardcoded based on trigger type, since there was no obvious way to generalize.
            # FreeTrigger does not broadcast, since that could end up being a bunch of stuff at the start of the seed.
            if isinstance(self.trigger, LocationTrigger):
                finding_player = self.trigger.player
                recipients = self.hint.get_recipients()
                hint_parts = self.hint.re_check(ctx, team).as_message_parts()
                
                messages = {}
                parts = []
                if finding_player in recipients and len(recipients) == 1:
                    add_json_text(parts, "Found own hint: ")
                elif len(recipients) == 1:
                    add_json_text(parts, "Found hint for ")
                    add_json_text(parts, recipients[0], type=JSONTypes.player_id)
                    add_json_text(parts, ": ")
                else:
                    # We could expand this out to a player list. But right now we don't even have any multi-target hints.
                    add_json_text(parts, "Found hint for multiple players: ")
                messages[finding_player] = parts + hint_parts

                for recipient in recipients:
                    parts = []
                    if recipient != finding_player:
                        add_json_text(parts, finding_player, type=JSONTypes.player_id)
                        add_json_text(parts, " found your hint: ")
                        messages[recipient] = parts + hint_parts
                ctx.notify_triggered_hints(team, messages)

class TriggeredHint:
    @staticmethod
    def get_team_data_for_type(ctx, team, hint_type):
        return ctx.triggerable_hint_state["team_data"][team].get(hint_type, {})
    
    def index(self, ctx, parent_triggerable_hint):
        pass

    def init_team_data(self, ctx, team, triggerable_hint):
        hint_data = ctx.triggerable_hint_state["team_data"][team].setdefault(type(self), {}).setdefault(self, {})
        hint_data["release_state"] = "unreleased"
        hint_data["release_data"] = None

    def get_team_data(self, ctx, team):
        return ctx.triggerable_hint_state["team_data"][team][type(self)][self]
    
    def release(self, ctx, team) -> bool:
        if self.get_team_data(ctx, team)["release_state"] == "unreleased":
            self.get_team_data(ctx, team)["release_state"] = "stale"
            return True
        return False
    
    def check_and_set_broadcasted(self, ctx, team) -> bool:
        if self.get_team_data(ctx, team)["release_state"] == "stale":
            self.re_check(ctx, team)
        if self.get_team_data(ctx, team)["release_state"] == "fresh":
            self.get_team_data(ctx, team)["release_state"] = "broadcasted"
            return True
        return False
        

    def mark_stale(self, ctx, team):
        if self.get_team_data(ctx, team)["release_state"] != "unreleased":
            self.get_team_data(ctx, team)["release_state"] = "stale"

    def is_released(self, ctx, team):
        return self.get_team_data(ctx, team)["release_state"] != "unreleased"

    def re_check(self, ctx, team) -> TriggeredHint:
        hint_data = self.get_team_data(ctx, team)

        if hint_data["release_state"] == "unreleased":
            raise Exception("Called re_check on unreleased hint")
        
        if hint_data["release_state"] == "stale":
            hint_data["release_data"] = self.get_release_data(ctx, team)
            hint_data["release_state"] = "fresh"
        
        # release_state should be fresh or broadcasted here
        return hint_data["release_data"]
    
    def get_recipients(self, team):
        raise NotImplementedError("Need to define who the hint is for")
        
    def get_release_data(self, ctx, team) -> TriggeredHint:
        # Returns a copy to release to the clients. This allows specializing with dynamic data, holding back stuff the client hasn't earned yet, etc.
        return self

    def as_message_parts(self):
        raise NotImplementedError("Need a way to send the hint to the text log")

@dataclass(frozen=True)
class TextHint(TriggeredHint):
    player: int
    text: str

    def get_recipients(self,):
        return [self.player]
    
    def as_message_parts(self):
        parts = []
        add_json_text(parts, self.text)
        return parts

    def __hash__(self):
        return hash((self.text, self.player))

@dataclass(frozen=True)
class LocationSetHint(TriggeredHint):
    player: int
    label: str
    set_kind: str  # Indicates semantics, as well as what will be in per_location_data
    total_value: int
    per_location_data: typing.Dict[int, typing.Tuple[int, object]]  # Maps locations to a tuple containing point value and any other data interesting for tracking (e.g. item/player ID). Released to clients once location is checked.
    
    @staticmethod
    def update_for_location_check(ctx, team, player, location):
        # We index the locations listed in per_location_data, and when those locations are checked, we need to give clients an updated version of the hint.
        for triggerable_hint in ctx.triggerable_hint_state.get("indexes", {}).get(LocationSetHint, {}).get(player, {}).get(location, []):
            triggerable_hint.hint.mark_stale(ctx, team)

    def index(self, ctx, parent_triggerable_hint):
        player_data = ctx.triggerable_hint_state.setdefault("indexes", {}).setdefault(LocationSetHint, {}).setdefault(self.player, {})
        for location in self.per_location_data.keys():
            player_data.setdefault(location, []).append(parent_triggerable_hint)

    def get_release_data(self, ctx, team):
        # Before releasing to the client, we need to filter down to just the locations they've actually checked. That gives them a current point total.
        filtered_per_location_data = {location : location_data for (location, location_data) in self.per_location_data.items() if location in ctx.location_checks[team, self.player]}
        return LocationSetHint(self.player, self.label, self.set_kind, self.total_value, filtered_per_location_data)

    def get_recipients(self,):
        return [self.player]
    
    def as_message_parts(self):

        if self.set_kind == "region_items" or self.set_kind == "region_hints":
            found_points = 0
            for location_data in self.per_location_data.values():
                found_points += location_data[0]

            parts = []
            add_json_text(parts, self.label)
            add_json_text(parts, " has ")
            add_json_text(parts, str(self.total_value))
            add_json_text(parts, " points of ")
            add_json_text(parts, "items, " if self.set_kind == "region_items" else "hints, ")
            add_json_text(parts, str(found_points))
            add_json_text(parts, " found.")
            return parts
        raise Exception("Unknown LocationSetHint set_kind")

    def __hash__(self):
        return hash((self.player, self.label, self.set_kind, self.total_value))

class Trigger:
    def index(self, ctx, parent_triggerable_hint):
        raise NotImplementedError("Triggers aren't very useful if not indexed")

@dataclass(frozen=True)
class FreeTrigger(Trigger):
    @staticmethod
    def release_all(ctx, team):
        for triggerable_hint in ctx.triggerable_hint_state.get("indexes", {}).get(FreeTrigger, []):
            triggerable_hint.release(ctx, team)

    def index(self, ctx, parent_triggerable_hint):
        free_data = ctx.triggerable_hint_state.setdefault("indexes", {}).setdefault(FreeTrigger, [])
        free_data.append(parent_triggerable_hint)

@dataclass(frozen=True)
class LocationTrigger(Trigger):
    player: int
    location: int
    
    @staticmethod
    def release_for_location_check(ctx, team, player, location):
        for triggerable_hint in ctx.triggerable_hint_state.get("indexes", {}).get(LocationTrigger, {}).get(player, {}).get(location, []):
            triggerable_hint.release(ctx, team)
    
    def index(self, ctx, parent_triggerable_hint):
        location_data = ctx.triggerable_hint_state.setdefault("indexes", {}).setdefault(LocationTrigger, {}).setdefault(self.player, {}).setdefault(self.location, [])
        location_data.append(parent_triggerable_hint)

class _LocationStore(dict, typing.MutableMapping[int, typing.Dict[int, typing.Tuple[int, int, int]]]):
    def __init__(self, values: typing.MutableMapping[int, typing.Dict[int, typing.Tuple[int, int, int]]]):
        super().__init__(values)

        if not self:
            raise ValueError(f"Rejecting game with 0 players")

        if len(self) != max(self):
            raise ValueError("Player IDs not continuous")

        if len(self.get(0, {})):
            raise ValueError("Invalid player id 0 for location")

    def find_item(self, slots: typing.Set[int], seeked_item_id: int
                  ) -> typing.Generator[typing.Tuple[int, int, int, int, int], None, None]:
        for finding_player, check_data in self.items():
            for location_id, (item_id, receiving_player, item_flags) in check_data.items():
                if receiving_player in slots and item_id == seeked_item_id:
                    yield finding_player, location_id, item_id, receiving_player, item_flags

    def get_for_player(self, slot: int) -> typing.Dict[int, typing.Set[int]]:
        import collections
        all_locations: typing.Dict[int, typing.Set[int]] = collections.defaultdict(set)
        for source_slot, location_data in self.items():
            for location_id, values in location_data.items():
                if values[1] == slot:
                    all_locations[source_slot].add(location_id)
        return all_locations

    def get_checked(self, state: typing.Dict[typing.Tuple[int, int], typing.Set[int]], team: int, slot: int
                    ) -> typing.List[int]:
        checked = state[team, slot]
        if not checked:
            # This optimizes the case where everyone connects to a fresh game at the same time.
            return []
        return [location_id for
                location_id in self[slot] if
                location_id in checked]

    def get_missing(self, state: typing.Dict[typing.Tuple[int, int], typing.Set[int]], team: int, slot: int
                    ) -> typing.List[int]:
        checked = state[team, slot]
        if not checked:
            # This optimizes the case where everyone connects to a fresh game at the same time.
            return list(self[slot])
        return [location_id for
                location_id in self[slot] if
                location_id not in checked]

    def get_remaining(self, state: typing.Dict[typing.Tuple[int, int], typing.Set[int]], team: int, slot: int
                      ) -> typing.List[int]:
        checked = state[team, slot]
        player_locations = self[slot]
        return sorted([player_locations[location_id][0] for
                       location_id in player_locations if
                       location_id not in checked])


if typing.TYPE_CHECKING:  # type-check with pure python implementation until we have a typing stub
    LocationStore = _LocationStore
else:
    try:
        from _speedups import LocationStore
        import _speedups
        import os.path
        if os.path.isfile("_speedups.pyx") and os.path.getctime(_speedups.__file__) < os.path.getctime("_speedups.pyx"):
            warnings.warn(f"{_speedups.__file__} outdated! "
                          f"Please rebuild with `cythonize -b -i _speedups.pyx` or delete it!")
    except ImportError:
        try:
            import pyximport
            pyximport.install()
        except ImportError:
            pyximport = None
        try:
            from _speedups import LocationStore
        except ImportError:
            warnings.warn("_speedups not available. Falling back to pure python LocationStore. "
                          "Install a matching C++ compiler for your platform to compile _speedups.")
            LocationStore = _LocationStore
