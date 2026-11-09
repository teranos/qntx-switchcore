# qntx-switchcore

Switch Rooms inside a QNTX node: `switchai-py` is a QNTX plugin that carries
[switch-core](https://github.com/sandbox-quantum/switch) (Switch's Python core) in
its own process and serves it to the node as the provider of **Rooms**
(QNTX [ADR-057](https://github.com/teranos/QNTX/blob/main/docs/adr/ADR-057-rooms.md)).

"the point of rooms is to implement the switch.ai way of doing rooms"

"rooms will be plugin provided"

## How Switch is used

**Which agents are connected.** The node's own agents, the ones QNTX already runs:
the Node Agent in each harness — **Claude Code** (`claude`), **Codex** (`codex`)
and **agy** (Gemini, `agy`) — plus `qntx-node`, the node itself. Each is
registered in switch-core as an agent by name (`handlers/rooms.py`, `_register`);
the names come from the plugin's record (`node_agents`, default `claude,agy,codex`).
A person who speaks in a room speaks as the node; an agent the node names speaks
as itself (`_as`).

**How the channel is set up.** The channel is the **Node Room**, a Switch room
switch-core holds, with the node's agents in it. It is reached from the QNTX
Web UI and the QNTX App as the Rooms element: ROOT opens Rooms → Node Room, sees
what the room is connected to and which agents are in it, presses an agent to
address it (`@name`), sends; the message reaches the room; an agent answers
into it. On a phone the App opens straight into the Node Room. The room is also
reachable from any MCP client through the node's `rooms_*` tools, and over HTTP
at `/api/rooms`. Connecting the Node Room to a Telegram group (`Room.connected`)
is designed and not yet wired. Slack, Teams and Discord are not used.

**What real work it does.** The agents in the Node Room are the agents that build
QNTX: the Node Agent that wrote this integration (pyre's `@rooms`, this handler,
the node's `rooms` signum) is one of them, and ROOT talks to them in the room
instead of opening one chat per agent.

## How it is built

- **pyre** is QNTX's Rust plugin runtime for Python handlers. Its `@rooms` route
  declares `rooms_provider` and serves the node's `RoomsService`
  ([teranos/pyre](https://github.com/teranos/pyre), #8); handlers ship inside
  the plugin's build (#10).
- **switch-core** is built with Nix (`switchai-py/nix/switch-core.nix`, via uv2nix)
  into a venv the plugin's interpreter sees; the handler starts it on a
  background thread (`_serve`) inside the plugin process and talks to it on
  `127.0.0.1` (`port`, default 8790). Its registration token, keyring and admin
  are made once and kept in `state_dir` (`_keys`).
- **The handler** (`switchai-py/handlers/rooms.py`) maps the node's verbs —
  `list`, `get`, `read`, `post`, `wake` — onto switch-core's HTTP API
  (`/agents`, `/agents/{id}/ops/{op}`). pyre runs the module again on every call;
  what has to last lives on `builtins`.
- Pinned inputs (`switchai-py/flake.nix`): pyre, the QNTX protocol pyre builds
  against, and `sandbox-quantum/switch`.

## How to run it

1. A QNTX node ([teranos/QNTX](https://github.com/teranos/QNTX)) and PostgreSQL on
   the same box: a login role (default `root`) that owns a database (default
   `switchai`). No extensions are needed.
2. Build: `cd switchai-py && nix build .#switchai-py`. CI
   (`.github/workflows/switchai-py.yml`) builds the same and packages an archive
   the node can take.
3. On the node, as ROOT (QNTX ADR-002):
   - `plugins_add https://github.com/teranos/qntx-switchcore/tree/main/switchai-py`
     — the last segment is the plugin's name, `switchai-py`.
   - `PUT /api/plugins/switchai-py/config` with what the handler reads
     (`db_host`, `db_port`, `db_user`, `db_name`, `state_dir`, `server_name`,
     `port`, `node_room`, `node_agents`; every one has a default) and the
     `build.*` keys that point the node at this repository.
   - `plugins_enable switchai-py`. A push to this repository, once its CI
     passes, is built and installed by the node itself.
4. `GET /api/rooms` lists the Node Room; the first call starts switch-core, which
   migrates and boots (up to two minutes). Open Rooms in the QNTX Web UI, or call
   `rooms_list`, `rooms_read`, `rooms_post`, `rooms_wake` over MCP.

## State of it

Rooms answer end to end from switch-core on the owner's node: list, get, read,
post, wake; the plugin survived a reinstall and two restarts in a day. A wake
posts the mention into the room, and what answers it today is switch-core's own
canned reply for that name, not the named Node Agent's harness: the turn it
should start is being wired. Every post shows as the node (`qntx-node`), a
person's and an agent's alike, until who-posts is carried through. On the
owner's phone the App opens into the Node Room.
