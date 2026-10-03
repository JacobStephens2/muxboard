# Muxboard

A control plane for tmux: inventory sessions on one host or a fleet, and attach to them from a browser.

## Language

**Board**:
One muxboard instance: the inventory of Hosts it manages and the Attach policy it applies.
_Avoid_: app, deployment, console

**Host**:
A named tmux server muxboard is allowed to inventory. One machine, or one non-default socket on a machine.
_Avoid_: box, server, node

**Tmux user**:
A Unix account whose tmux socket a Host may list, create, kill, and attach.
_Avoid_: login, ssh user (that is how muxboard *reaches* the Host, not whose sessions it shows)

**Session**:
A tmux session on a Host, identified by name.
_Avoid_: job, agent, tab

**Dashboard**:
The HTML list of Hosts, tmux users, and Sessions.
_Avoid_: console (legacy URL)

**Attach**:
A live, interactive browser view of one Session.
_Avoid_: terminal tab, replay, log view

**Kill**:
An operator action that ends a Session on its Host. The request echoes the Session name as confirmation; a mismatch is refused.
_Avoid_: delete, terminate, close

**Selection**:
The operator-highlighted span of text in an Attach. Muxboard owns this highlight; the browser's native text selection does not.
_Avoid_: native selection, highlight

**Copy**:
An operator action that puts the current Selection on the system clipboard. Empty Selection is not Copy. A Selection that is a hard-wrapped `http`/`https` URL is copied as one URL (newlines stripped); any other Selection is copied as highlighted.
_Avoid_: dump, export, capture

**Link**:
An `http` or `https` URL in Attach output, including one the Session hard-wrapped at the pane width. The operator opens it with a modifier-click (Ctrl+click, Cmd+click on macOS) into a new tab.
_Avoid_: hyperlink, web-link

**Clipboard push**:
The Session writes the operator's clipboard. Muxboard notices and refuses oversize payloads. Off unless the Board turns it on.
_Avoid_: OSC 52 write, set-clipboard

**Clipboard query**:
The Session asks to read the operator's clipboard. Muxboard prompts; Allow is required. Off unless the Board turns it on.
_Avoid_: OSC 52 read, paste from browser
