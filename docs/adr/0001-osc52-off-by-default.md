# OSC 52 is off unless the Board opts in

Attach is a remote shell. Letting the Session write or read the operator clipboard is a real trade (Claude login URLs, tmux copy-mode) against a real steal (anything that can print to the pane can OSC 52 the clipboard, and a query types clipboard contents into the Session). `Muxboard(..., osc52="off"|"write"|"read-write")` defaults to `"off"`. Query always prompts; push is noticed and capped at 64 KiB.

**Considered Options**: always-on read+write (addon default); write-on/read-off as the library default; dashboard-only JS fork with no constructor flag.
