# switchai-py's rooms (QNTX ADR-057): switch-core, in this process, holds them.
# pyre runs this module again for every call, so what has to last (switch-core
# running, the agents it registered) is kept on builtins between calls.

import builtins
import datetime
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PROVIDER = "switchai-py"
REACH_WAITS = 2

# The plugin's record on the node may name each of these; unnamed, switch-core's
# database is reached over the local socket as the plugin's own user, peer
# authenticated, and its keys are the plugin's own (_keys).
DEFAULTS = {
    "db_host": "/var/run/postgresql",
    "db_port": "5432",
    "db_user": "root",
    "db_password": "peer",
    "db_name": "switchai",
    "state_dir": "/var/lib/qntx/switchai-py",
    "server_name": "localhost",
    "port": "8790",
    "node_room": "Node Room",
    "node_agents": "claude,agy,codex",
}


def _config(key):
    held = globals().get("plugin_config") or {}
    return held.get(key) or DEFAULTS.get(key, "")


def _keys():
    # switch-core's registration token, keyring and admin, made once and kept
    # beside its data, readable by the plugin's user alone.
    path = os.path.join(_config("state_dir"), "keys.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    os.makedirs(_config("state_dir"), mode=0o700, exist_ok=True)
    made = {
        "registration_token": secrets.token_urlsafe(32),
        "secret_keys": "k1:" + secrets.token_urlsafe(48),
        "admin_email": "tim@example.com",
        "admin_password": secrets.token_urlsafe(32),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(made, f)
    return made


def _key(name):
    return _config(name) or _keys()[name]


def _held():
    held = getattr(builtins, "_switchai", None)
    if held is None:
        held = {"lock": threading.Lock(), "started": False, "agents": {}, "node_room": None}
        builtins._switchai = held
    return held


def _http(method, path, body=None, key=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request("http://127.0.0.1:" + _config("port") + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if key:
        request.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            raw = answer.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"switch-core answered {method} {path} with {e.code}: {e.read().decode(errors='replace')}")


def _serve():
    # switch-core's run() asks its loop for signal handlers, which only the main
    # thread's loop gives; a loop that takes none is a case switch-core handles.
    import asyncio

    from switch_core import main as core
    from switch_core.config import SwitchConfig
    from switch_core.process_identity import new_process_id

    class _Loop(asyncio.SelectorEventLoop):
        def add_signal_handler(self, *args, **kwargs):
            raise NotImplementedError

    config = SwitchConfig()
    asyncio.run(core._migrate_and_grant(config))
    asyncio.run(core.run(config, new_process_id(config.pod_name)), loop_factory=_Loop)


def _start():
    held = _held()
    with held["lock"]:
        if held["started"]:
            return
        env = {
            "DB_HOST": _config("db_host"),
            "DB_PORT": _config("db_port"),
            "DB_USER": _config("db_user"),
            "DB_PASSWORD": _config("db_password"),
            "DB_NAME": _config("db_name"),
            "DB_REQUIRE_RESTRICTED_ROLE": "false",
            "ID_SERVER_NAME": _config("server_name"),
            "AGENT_REGISTRATION_TOKEN": _key("registration_token"),
            "SECRET_KEYS": _key("secret_keys"),
            "GATEWAY_ADMIN_EMAIL": _key("admin_email"),
            "GATEWAY_ADMIN_PASSWORD": _key("admin_password"),
            "SERVER_HOST": "127.0.0.1",
            "SERVER_PORT": _config("port"),
        }
        os.environ.update(env)
        thread = threading.Thread(target=_serve, name="switch-core", daemon=True)
        thread.start()
        deadline = time.time() + 120
        while time.time() < deadline:
            if not thread.is_alive():
                raise RuntimeError("switch-core stopped while starting: the plugin's log says why")
            try:
                _http("GET", "/health")
                held["started"] = True
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError("switch-core did not answer /health within 120s")


def _register(name, description=""):
    # Registered over itself, so a restarted plugin holds a key that works.
    held = _held()
    if name in held["agents"]:
        return held["agents"][name]
    registered = _http("POST", "/agents", {
        "name": name,
        "description": description or name,
        "connector_type": "qntx",
        "integration_profile": {"connection_model": "session_passive", "message_exchange": True,
                                "pre_invocation_mediation": [], "post_invocation_mediation": [],
                                "event_reporting": []},
        "overwrite": True,
    }, key=_key("registration_token"))
    held["agents"][name] = {"id": registered["id"], "key": registered["api_key"]}
    return held["agents"][name]


def _node():
    # The node's own agent in switch-core, through which the node asks.
    return _register("qntx-node", "the QNTX node")


def _op(agent, op, body):
    return _http("POST", f"/agents/{agent['id']}/ops/{op}", body, key=agent["key"]).get("result")


def _as(by):
    # An agent the node names asks as itself; a person, as the node.
    name = (by or {}).get("name") or ""
    held = _held()
    if name and name in held["agents"]:
        return held["agents"][name]
    return _node()


def _room(listed):
    return {
        "id": listed.get("room_id", ""),
        "provider": PROVIDER,
        "name": listed.get("name", ""),
        "description": listed.get("description") or "",
    }


def _rooms():
    return _op(_node(), "list_rooms", {}) or []


def _node_room():
    # Every Node Agent is in the Node Room.
    held = _held()
    if held["node_room"]:
        return held["node_room"]
    name = _config("node_room")
    agents = [a.strip() for a in _config("node_agents").split(",") if a.strip()]
    for each in agents:
        _register(each, "a Node Agent of this node")
    for listed in _rooms():
        if listed.get("name") == name:
            held["node_room"] = listed.get("room_id")
            return held["node_room"]
    made = _op(_node(), "create_room", {"name": name, "description": "the Node Agents of this node",
                                         "agent_names": ["qntx-node"] + agents})
    held["node_room"] = made["id"]
    return held["node_room"]


def _at(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _post(by, room, content):
    sender = _as(by)
    posted = _http("POST", f"/agents/{sender['id']}/message", {"room_id": room, "content": content, "metadata": {}}, key=sender["key"])
    return posted.get("event_id", "")


def answer(call, request):
    _start()
    _node_room()
    if call == "RegisterRoomAgent":
        agent = request.get("agent") or {}
        _register(agent.get("name", ""), request.get("description", ""))
        return {"registered": agent}
    if call == "CreateRoom":
        names = ["qntx-node"] + [a.get("name") for a in request.get("agents", []) if a.get("name")]
        made = _op(_as(request.get("by")), "create_room", {"name": request.get("name", ""),
                                                          "description": request.get("description", ""),
                                                          "instructions": request.get("instructions", ""),
                                                          "agent_names": names})
        return {"created": _room({"room_id": made["id"], "name": request.get("name", ""),
                                  "description": request.get("description", "")})}
    if call == "ListRooms":
        return {"list": [_room(r) for r in _rooms()]}
    if call == "GetRoom":
        found = [r for r in _rooms() if r.get("room_id") == request.get("room")]
        if not found:
            return {"error": f"switchai-py holds no room {request.get('room')}"}
        people = _http("GET", f"/agents/rooms/{request['room']}/participants", key=_node()["key"])
        agents = [{"name": p.get("name", ""), "harness": "switch"} for p in people.get("participants", []) if p.get("type") == "agent"]
        return {"room": _room(found[0]), "agents": agents}
    if call == "InvolveInRoom":
        agent = request.get("agent") or {}
        _register(agent.get("name", ""))
        _op(_as(request.get("by")), "invite_agent_to_room", {"room_id": request.get("room"), "agent_name": agent.get("name")})
        return {"involved": agent}
    if call == "PostToRoom":
        return {"id": _post(request.get("by"), request.get("room"), request.get("body", ""))}
    if call == "WakeInRoom":
        agents = request.get("agents", [])
        said = " ".join("@" + a.get("name", "") for a in agents) + " " + request.get("says", "")
        posted = _post(request.get("by"), request.get("room"), said.strip())
        # Whether a mention reaches an agent is its own addressing policy's: it stands in the room.
        return {"id": posted, "woken": [{"agent": a, "reach": REACH_WAITS} for a in agents]}
    if call == "ReadRoom":
        query = {k: request[k] for k in ("limit", "since", "before") if request.get(k)}
        reader = _node()
        path = f"/agents/{reader['id']}/rooms/{request.get('room')}/history"
        if query:
            path += "?" + urllib.parse.urlencode(query)
        read = _http("GET", path, key=reader["key"])
        messages = [{"room": request.get("room"), "sender": e.get("sender", ""), "sender_name": e.get("sender_name", ""),
                     "body": e.get("body", ""), "at": _at(e["timestamp"]) if e.get("timestamp") else ""}
                    for e in read.get("events", [])]
        return {"messages": messages, "truncated": bool(read.get("has_more"))}
    return {"error": f"switchai-py does not answer {call}"}


@rooms()
def held(asked):
    try:
        return answer(asked.get("call", ""), asked.get("request") or {})
    except Exception as e:
        return {"error": str(e)}
