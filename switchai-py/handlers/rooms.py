# switchai-py's rooms (QNTX ADR-057): switch-core, in this process, holds them.
# pyre runs this module again for every call, so what has to last (switch-core
# running, the agents it registered) is kept on builtins between calls.

import builtins
import datetime
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PROVIDER = "switchai-py"
REACH_WAITS = 2

# The plugin's record on the node may name each of these; unnamed, switch-core's
# database is the node's PostgreSQL on 127.0.0.1 as the plugin's own user, and
# its keys, the database password among them, are the plugin's own (_keys).
DEFAULTS = {
    "db_host": "127.0.0.1",
    "db_port": "5432",
    "db_user": "root",
    "db_name": "switchai",
    "state_dir": "/var/lib/qntx/switchai-py",
    "server_name": "localhost",
    "port": "8790",
    "node_room": "Node Room",
    "node_agents": "claude,agy,codex",
    # Switch Trust's guardrails, which switch-core checks every message against.
    # Without a policy id and a key, nothing is applied and what switch-core
    # holds is left as it is.
    "trust_endpoint": "https://api.switchagents.ai",
    "trust_policy_id": "",
    "trust_api_key": "",
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
        "db_password": secrets.token_urlsafe(32),
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
        held = {"lock": threading.Lock(), "agents_lock": threading.RLock(), "started": False, "agents": {}, "node_room": None}
        builtins._switchai = held
    return held


def _http(method, path, body=None, key=None, raw=None, content_type="application/json"):
    # raw is a body sent as it is, content_type naming it; an answer that is not
    # JSON comes back as its bytes and its type.
    data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request("http://127.0.0.1:" + _config("port") + path, data=data, method=method)
    request.add_header("Content-Type", content_type)
    if key:
        request.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            read = answer.read()
            kind = answer.headers.get("Content-Type", "")
            if not kind.startswith("application/json"):
                return read, kind
            return json.loads(read) if read else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"switch-core answered {method} {path} with {e.code}: {e.read().decode(errors='replace')}")


def _serve():
    # switch-core's run() takes SIGINT and SIGTERM, which only the main thread
    # may. Here the node stops the plugin's process, switch-core with it, so its
    # loop takes no signal handlers and its main module installs none.
    import asyncio
    import signal
    import types

    from switch_core import main as core
    from switch_core.config import SwitchConfig
    from switch_core.process_identity import new_process_id

    class _Loop(asyncio.SelectorEventLoop):
        def add_signal_handler(self, *args, **kwargs):
            raise NotImplementedError

    core.signal = types.SimpleNamespace(SIGINT=signal.SIGINT, SIGTERM=signal.SIGTERM, signal=lambda *args: None)

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
            "DB_PASSWORD": _key("db_password"),
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
            except Exception:
                time.sleep(1)
                continue
            held["started"] = True
            try:
                _trust()
            except Exception as e:
                # Rooms work without guardrails; the plugin's log says why not.
                print(f"switchai-py: Switch Trust was not applied: {e}", flush=True)
            return
        raise RuntimeError("switch-core did not answer /health within 120s")


def _trust():
    # The node's record names the policy and holds the key, so they outlast
    # switch-core's database, where it keeps them: they are put there again only
    # where they differ.
    keys = _keys()
    policy = _config("trust_policy_id")
    key = _config("trust_api_key")
    if not policy or not key:
        return
    endpoint = _config("trust_endpoint")
    base = "http://127.0.0.1:" + _config("port") + "/gateway"
    login = urllib.request.Request(base + "/auth/login", method="POST",
                                   data=json.dumps({"email": keys["admin_email"], "password": keys["admin_password"]}).encode())
    login.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(login, timeout=60) as answer:
        # The session cookie is Secure, which urllib will not send over http, so
        # it goes back as it came.
        cookies = "; ".join(c.split(";", 1)[0] for c in answer.headers.get_all("Set-Cookie") or [])

    def call(method, body=None):
        request = urllib.request.Request(base + "/trust-settings", method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
        request.add_header("Content-Type", "application/json")
        request.add_header("Cookie", cookies)
        with urllib.request.urlopen(request, timeout=60) as answer:
            return json.loads(answer.read() or "{}")

    held = call("GET")
    if (held.get("endpoint") == endpoint and held.get("policy_id") == policy
            and held.get("has_api_key") and held.get("api_key_last4") == key[-4:]):
        return
    call("PUT", {"endpoint": endpoint, "policy_id": policy, "api_key": key})


def _agents_file():
    return os.path.join(_config("state_dir"), "agents.json")


def _kept_agents():
    # The agents switch-core holds for the node, with their keys, kept across
    # restarts: registering one again would give it a new key, and leave every
    # other holder of the old one refused.
    path = _agents_file()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _keep_agent(name, agent):
    # Under agents_lock, held by _register: two writers would interleave.
    kept = _kept_agents()
    kept[name] = agent
    _keep_all(kept)


def _keep_all(kept):
    path = _agents_file()
    fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(kept, f)
    os.replace(path + ".new", path)


def _register(name, description="", display_name=None):
    held = _held()
    with held["agents_lock"]:
        return _register_held(held, name, description, display_name)


def _register_held(held, name, description, display_name):
    if name in held["agents"]:
        return held["agents"][name]
    kept = _kept_agents().get(name)
    if kept:
        held["agents"][name] = kept
        return kept
    registered = _http("POST", "/agents", {
        "name": name,
        "display_name": display_name or name,
        "description": description or name,
        "connector_type": "qntx",
        "integration_profile": {"connection_model": "session_passive", "message_exchange": True,
                                "pre_invocation_mediation": [], "post_invocation_mediation": [],
                                "event_reporting": []},
        "overwrite": True,
    }, key=_key("registration_token"))
    held["agents"][name] = {"id": registered["id"], "key": registered["api_key"]}
    _keep_agent(name, held["agents"][name])
    return held["agents"][name]


def _node():
    # The node's own agent in switch-core, through which the node asks.
    return _register("qntx-node", "the QNTX node")


def _op(agent, op, body):
    return _http("POST", f"/agents/{agent['id']}/ops/{op}", body, key=agent["key"]).get("result")


def _slug(name):
    # switch-core's agent names are lower case letters, digits and dashes.
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:32]


def _as(by, room=None):
    # Whoever the node names asks as themselves: an agent it holds by its name,
    # and a person, or a token, registered under theirs and brought into the
    # room. The node itself asks as qntx-node.
    by = by or {}
    name = by.get("name") or ""
    held = _held()
    if name in held["agents"]:
        return held["agents"][name]
    slug = _slug(name or by.get("did", ""))
    if not slug:
        return _node()
    who = _register(slug, "a person at the node", display_name=name or None)
    if room and (slug, room) not in held.setdefault("in_rooms", set()):
        try:
            _op(_node(), "invite_agent_to_room", {"room_id": room, "agent_name": slug})
        except RuntimeError as e:
            if "already" not in str(e).lower() and "409" not in str(e):
                raise
        held["in_rooms"].add((slug, room))
    return who


def _room(listed):
    return {
        "id": listed.get("room_id", ""),
        "provider": PROVIDER,
        "name": listed.get("name", ""),
        "description": listed.get("description") or "",
    }


def _rooms():
    return _op(_node(), "list_rooms", {}) or []


def _live(name):
    # The agents the node runs answer in the room through the node, which wakes
    # them; held live here, switch-core does not answer for them with its own
    # line for an agent nobody runs.
    held = _held()
    live = held.setdefault("live", {})
    if live.get(name) and live[name].is_alive():
        return
    agent = held["agents"][name]

    def hold():
        import asyncio

        import websockets

        async def connected():
            url = f"ws://127.0.0.1:{_config('port')}/agents/{agent['id']}/connection/ws?scope=all"
            while True:
                try:
                    async with websockets.connect(url, additional_headers={"Authorization": "Bearer " + agent["key"]}) as ws:
                        cursor = 0
                        async for raw in ws:
                            frame = json.loads(raw)
                            cursor = frame.get("id") or cursor
                            if frame.get("event") == "ping":
                                await ws.send(json.dumps({"type": "pong", "cursor": cursor}))
                            elif frame.get("event") == "refused":
                                held.setdefault("refused", {})[name] = frame.get("data")
                except Exception as e:
                    held.setdefault("dropped", {})[name] = f"{type(e).__name__}: {e}"
                await asyncio.sleep(5)

        asyncio.run(connected())

    live[name] = threading.Thread(target=hold, name=f"switch-live-{name}", daemon=True)
    live[name].start()


def _node_room():
    # Every Node Agent is in the Node Room.
    held = _held()
    if held["node_room"]:
        return held["node_room"]
    name = _config("node_room")
    agents = [a.strip() for a in _config("node_agents").split(",") if a.strip()]
    for each in agents:
        _register(each, "a Node Agent of this node")
        _live(each)
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


def _file_data(data):
    # The node's bytes arrive as a list of numbers, or as base64.
    if isinstance(data, str):
        import base64

        return base64.b64decode(data)
    return bytes(data or [])


def _post(by, room, content, files=None, thread=""):
    sender = _as(by, room)
    if not files:
        posted = _http("POST", f"/agents/{sender['id']}/message", {"room_id": room, "content": content, "metadata": {}}, key=sender["key"])
        return posted.get("event_id", "")
    # Files go through switch-core's media route, the body their caption.
    boundary = secrets.token_hex(16)
    parts = []
    fields = {"caption": content, "thread_id": thread}
    for field, value in fields.items():
        if value:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode())
    for each in files:
        name = (each.get("name") or "file").replace('"', "")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                     f'Content-Type: {each.get("type") or "application/octet-stream"}\r\n\r\n'.encode()
                     + _file_data(each.get("data")) + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    posted = _http("POST", f"/agents/{sender['id']}/rooms/{room}/media", raw=b"".join(parts), key=sender["key"],
                   content_type="multipart/form-data; boundary=" + boundary)
    return posted.get("event_id", "")


def _attachments(room, event):
    # Each attachment is served by the node, through this plugin's media route.
    return [{"name": a.get("filename", ""), "type": a.get("mimetype", ""),
             "url": f"/api/{PROVIDER}/media?" + urllib.parse.urlencode({"room": room, "mxc": a.get("mxc", "")})}
            for a in event.get("attachments") or [] if a.get("mxc")]


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
            return {"error": {"code": "NOT_FOUND", "message": f"switchai-py holds no room {request.get('room')}"}}
        people = _http("GET", f"/agents/rooms/{request['room']}/participants", key=_node()["key"])
        agents = [{"name": p.get("name", ""), "harness": "switch"} for p in people.get("participants", []) if p.get("type") == "agent"]
        return {"room": _room(found[0]), "agents": agents}
    if call == "InvolveInRoom":
        agent = request.get("agent") or {}
        _register(agent.get("name", ""))
        _op(_as(request.get("by")), "invite_agent_to_room", {"room_id": request.get("room"), "agent_name": agent.get("name")})
        return {"involved": agent}
    if call == "PostToRoom":
        return {"id": _post(request.get("by"), request.get("room"), request.get("body", ""),
                            request.get("files"), request.get("thread", ""))}
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
        messages = [{"id": e.get("id") or "", "room": request.get("room"), "sender": e.get("sender", ""),
                     "sender_name": e.get("sender_name", ""), "body": e.get("body", ""),
                     "at": _at(e["timestamp"]) if e.get("timestamp") else "",
                     "attachments": _attachments(request.get("room"), e)}
                    for e in read.get("events", [])]
        return {"messages": messages, "truncated": bool(read.get("has_more"))}
    return {"error": {"code": "INVALID_ARGUMENT", "message": f"switchai-py does not answer {call}"}}


def _forget_all():
    # Keys switch-core refuses: every agent is registered again on the next ask.
    held = _held()
    with held["agents_lock"]:
        held["agents"].clear()
        held["in_rooms"] = set()
        _keep_all({})


@route("GET", "/media")
def media(request):
    # An attachment's bytes, as the node's own agent in switch-core reads them.
    asked = urllib.parse.parse_qs(request.get("query", ""))
    room, mxc = (asked.get("room") or [""])[0], (asked.get("mxc") or [""])[0]
    if not room or not mxc:
        return {"status": 400, "headers": {"Content-Type": "text/plain"}, "body": "media takes room and mxc"}
    try:
        _start()
        reader = _node()
        data, kind = _http("GET", f"/agents/{reader['id']}/rooms/{room}/media?" + urllib.parse.urlencode({"mxc": mxc}),
                           key=reader["key"])
    except Exception as e:
        return {"status": 502, "headers": {"Content-Type": "text/plain"}, "body": f"{type(e).__name__}: {e}"}
    return {"status": 200, "headers": {"Content-Type": kind or "application/octet-stream"}, "body": data}


@rooms()
def held(asked):
    try:
        try:
            return answer(asked.get("call", ""), asked.get("request") or {})
        except RuntimeError as e:
            if " with 401" not in str(e):
                raise
            _forget_all()
            return answer(asked.get("call", ""), asked.get("request") or {})
    except Exception as e:
        return {"error": {"code": "UNAVAILABLE", "message": f"{type(e).__name__}: {e}"}}
