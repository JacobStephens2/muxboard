# Links open on modifier-click, not plain click

The job that motivated Links was a long login URL the operator also needs to **Select** and **Copy**. Plain-click-to-open fights that drag. Ctrl+click (Cmd+click on macOS) opens `http`/`https` in a new tab with `noopener,noreferrer`; hover shows the raw URL; OSC 8 uses the same rules.

**Considered Options**: addon default (plain click opens); confirm modal before `window.open`; Copy only, no Links.
